"""Unit tests for OpenAIEmbeddingAdapter Batch API path (Phase 25, SC-3)."""
from __future__ import annotations

import io
import json
import logging
import pathlib
from unittest.mock import MagicMock, call, patch

import pytest

import openai
from config.settings import EmbeddingConfig
from embeddings.openai_adapter import (
    OpenAIEmbeddingAdapter,
    OpenAIEmbeddingError,
    _build_jsonl,
    _parse_batch_output,
    _BATCH_MAX_INPUTS,
    _write_batch_checkpoint,
    _read_batch_checkpoint,
    _delete_batch_checkpoint,
)


# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------

def _make_batch(
    status: str,
    completed: int = 0,
    total: int = 0,
    id_: str = "batch_abc",
    output_file_id: str = "file_out_123",
) -> MagicMock:
    """Return a MagicMock that looks like an OpenAI Batch object."""
    batch = MagicMock()
    batch.id = id_
    batch.status = status
    batch.request_counts = MagicMock()
    batch.request_counts.completed = completed
    batch.request_counts.total = total
    batch.output_file_id = output_file_id
    return batch


def _make_file_obj(id_: str = "file_in_456") -> MagicMock:
    """Return a MagicMock that looks like an OpenAI File object."""
    f = MagicMock()
    f.id = id_
    return f


def _make_output_content(vectors: list[list[float]]) -> MagicMock:
    """Return a MagicMock with .text set to JSONL batch output (reversed order for reorder test)."""
    lines = []
    # Intentionally output in REVERSE order to verify reorder logic
    for i in reversed(range(len(vectors))):
        obj = {
            "custom_id": str(i),
            "response": {
                "body": {
                    "data": [{"embedding": vectors[i]}]
                }
            }
        }
        lines.append(json.dumps(obj))
    content = MagicMock()
    content.text = "\n".join(lines)
    return content


def _make_adapter(batch: bool = True) -> OpenAIEmbeddingAdapter:
    """Create an OpenAIEmbeddingAdapter with a mocked OpenAI client."""
    cfg = EmbeddingConfig(
        type="openai",
        model="text-embedding-3-small",
        api_key="sk-test-key",
        openai_batch=batch,
    )
    with patch("embeddings.openai_adapter.openai.OpenAI"):
        adapter = OpenAIEmbeddingAdapter(cfg)
    return adapter


# ---------------------------------------------------------------------------
# TestSupportsBatchApiProperty
# ---------------------------------------------------------------------------

class TestSupportsBatchApiProperty:
    def test_property_false_when_flag_unset(self):
        cfg = EmbeddingConfig(
            type="openai",
            model="text-embedding-3-small",
            api_key="sk-test-key",
            openai_batch=False,
        )
        with patch("embeddings.openai_adapter.openai.OpenAI"):
            adapter = OpenAIEmbeddingAdapter(cfg)
        assert adapter.supports_batch_api is False

    def test_property_true_when_flag_set(self):
        cfg = EmbeddingConfig(
            type="openai",
            model="text-embedding-3-small",
            api_key="sk-test-key",
            openai_batch=True,
        )
        with patch("embeddings.openai_adapter.openai.OpenAI"):
            adapter = OpenAIEmbeddingAdapter(cfg)
        assert adapter.supports_batch_api is True


# ---------------------------------------------------------------------------
# TestBuildJsonl
# ---------------------------------------------------------------------------

class TestBuildJsonl:
    def test_jsonl_one_line_per_text_with_correct_keys(self):
        texts = ["hello", "world", "test"]
        result = _build_jsonl(texts, "text-embedding-3-small")
        assert isinstance(result, bytes)
        lines = result.decode("utf-8").splitlines()
        assert len(lines) == 3
        for i, line in enumerate(lines):
            obj = json.loads(line)
            assert obj["custom_id"] == str(i)
            assert obj["method"] == "POST"
            assert obj["url"] == "/v1/embeddings"
            assert obj["body"]["model"] == "text-embedding-3-small"
            assert obj["body"]["encoding_format"] == "float"
            assert obj["body"]["input"] == texts[i]


# ---------------------------------------------------------------------------
# TestParseBatchOutput
# ---------------------------------------------------------------------------

class TestParseBatchOutput:
    def test_parse_reorders_by_custom_id(self):
        # Shuffled order: custom_id "2" first, then "0", then "1"
        vec0 = [0.1, 0.2]
        vec1 = [0.3, 0.4]
        vec2 = [0.5, 0.6]
        lines = [
            json.dumps({"custom_id": "2", "response": {"body": {"data": [{"embedding": vec2}]}}}),
            json.dumps({"custom_id": "0", "response": {"body": {"data": [{"embedding": vec0}]}}}),
            json.dumps({"custom_id": "1", "response": {"body": {"data": [{"embedding": vec1}]}}}),
        ]
        content_str = "\n".join(lines)
        result = _parse_batch_output(content_str, 3)
        assert result == [vec0, vec1, vec2]

    def test_parse_count_mismatch_raises(self):
        lines = [
            json.dumps({"custom_id": "0", "response": {"body": {"data": [{"embedding": [0.1]}]}}}),
            json.dumps({"custom_id": "1", "response": {"body": {"data": [{"embedding": [0.2]}]}}}),
        ]
        content_str = "\n".join(lines)
        with pytest.raises(OpenAIEmbeddingError, match="count mismatch"):
            _parse_batch_output(content_str, 3)

    def test_parse_handles_object_with_text_attr_and_plain_str(self):
        line = json.dumps({"custom_id": "0", "response": {"body": {"data": [{"embedding": [1.0, 2.0]}]}}})
        jsonl_str = line

        # Test with plain string
        result1 = _parse_batch_output(jsonl_str, 1)
        assert result1 == [[1.0, 2.0]]

        # Test with object that has .text attribute (like SDK returns)
        mock_content = MagicMock()
        mock_content.text = jsonl_str
        result2 = _parse_batch_output(mock_content, 1)
        assert result2 == [[1.0, 2.0]]


# ---------------------------------------------------------------------------
# TestBatchFullFlow
# ---------------------------------------------------------------------------

class TestBatchFullFlow:
    def test_full_flow_returns_vectors_in_order(self, tmp_path, monkeypatch):
        monkeypatch.setattr("embeddings.openai_adapter._BATCH_CHECKPOINT_DIR", tmp_path)

        cfg = EmbeddingConfig(
            type="openai",
            model="text-embedding-3-small",
            api_key="sk-test-key",
            openai_batch=True,
        )
        vectors = [[0.1, 0.2], [0.3, 0.4], [0.5, 0.6]]

        with patch("embeddings.openai_adapter.openai.OpenAI") as mock_cls:
            mock_client = MagicMock()
            mock_cls.return_value = mock_client

            mock_client.files.create.return_value = _make_file_obj("f_in_1")
            mock_client.batches.create.return_value = _make_batch(
                "validating", id_="b_1", output_file_id="f_out_1"
            )
            mock_client.batches.retrieve.side_effect = [
                _make_batch("in_progress", id_="b_1"),
                _make_batch("completed", completed=3, total=3, id_="b_1", output_file_id="f_out_1"),
            ]
            mock_client.files.content.return_value = _make_output_content(vectors)

            adapter = OpenAIEmbeddingAdapter(cfg)

            with patch("embeddings.openai_adapter.time.sleep"):
                result = adapter.embed_batch_async(["a", "b", "c"], collection_name="ProductsTest")

        assert result == vectors

    def test_checkpoint_written_before_poll_and_deleted_after(self, tmp_path, monkeypatch):
        monkeypatch.setattr("embeddings.openai_adapter._BATCH_CHECKPOINT_DIR", tmp_path)

        cfg = EmbeddingConfig(
            type="openai",
            model="text-embedding-3-small",
            api_key="sk-test-key",
            openai_batch=True,
        )
        vectors = [[0.1, 0.2], [0.3, 0.4]]
        checkpoint_existed_during_poll = []

        def check_checkpoint_exists(batch_id: str) -> MagicMock:
            # Verify checkpoint file exists the moment first poll is called
            ckpt_path = tmp_path / "ProductsTest.batch_checkpoint.json"
            checkpoint_existed_during_poll.append(ckpt_path.exists())
            return _make_batch("completed", completed=2, total=2, id_="b_chk", output_file_id="f_chk_out")

        with patch("embeddings.openai_adapter.openai.OpenAI") as mock_cls:
            mock_client = MagicMock()
            mock_cls.return_value = mock_client

            mock_client.files.create.return_value = _make_file_obj("f_in_chk")
            mock_client.batches.create.return_value = _make_batch(
                "validating", id_="b_chk", output_file_id="f_chk_out"
            )
            mock_client.batches.retrieve.side_effect = check_checkpoint_exists
            mock_client.files.content.return_value = _make_output_content(vectors)

            adapter = OpenAIEmbeddingAdapter(cfg)

            with patch("embeddings.openai_adapter.time.sleep"):
                adapter.embed_batch_async(["x", "y"], collection_name="ProductsTest")

        # Checkpoint existed during the first poll
        assert checkpoint_existed_during_poll[0] is True
        # Checkpoint was deleted after success
        assert _read_batch_checkpoint("ProductsTest") is None

    def test_empty_input_returns_empty(self, tmp_path, monkeypatch):
        monkeypatch.setattr("embeddings.openai_adapter._BATCH_CHECKPOINT_DIR", tmp_path)

        cfg = EmbeddingConfig(
            type="openai",
            model="text-embedding-3-small",
            api_key="sk-test-key",
            openai_batch=True,
        )

        with patch("embeddings.openai_adapter.openai.OpenAI") as mock_cls:
            mock_client = MagicMock()
            mock_cls.return_value = mock_client
            adapter = OpenAIEmbeddingAdapter(cfg)
            result = adapter.embed_batch_async([], collection_name="X")

        assert result == []
        mock_client.files.create.assert_not_called()
        mock_client.batches.create.assert_not_called()

    def test_too_many_inputs_raises(self, tmp_path, monkeypatch):
        monkeypatch.setattr("embeddings.openai_adapter._BATCH_CHECKPOINT_DIR", tmp_path)

        cfg = EmbeddingConfig(
            type="openai",
            model="text-embedding-3-small",
            api_key="sk-test-key",
            openai_batch=True,
        )

        with patch("embeddings.openai_adapter.openai.OpenAI"):
            adapter = OpenAIEmbeddingAdapter(cfg)
            with pytest.raises(OpenAIEmbeddingError) as exc_info:
                adapter.embed_batch_async(
                    ["x"] * (_BATCH_MAX_INPUTS + 1),
                    collection_name="TestCol",
                )
        msg = str(exc_info.value)
        assert "50,000" in msg or "50000" in msg


# ---------------------------------------------------------------------------
# TestBatchFailureModes
# ---------------------------------------------------------------------------

class TestBatchFailureModes:
    def test_failed_status_raises_with_masked_key(self, tmp_path, monkeypatch):
        monkeypatch.setattr("embeddings.openai_adapter._BATCH_CHECKPOINT_DIR", tmp_path)

        cfg = EmbeddingConfig(
            type="openai",
            model="text-embedding-3-small",
            api_key="sk-test-key",
            openai_batch=True,
        )

        with patch("embeddings.openai_adapter.openai.OpenAI") as mock_cls:
            mock_client = MagicMock()
            mock_cls.return_value = mock_client
            mock_client.files.create.return_value = _make_file_obj()
            mock_client.batches.create.return_value = _make_batch("validating", id_="b_fail")
            mock_client.batches.retrieve.return_value = _make_batch("failed", id_="b_fail")

            adapter = OpenAIEmbeddingAdapter(cfg)
            with patch("embeddings.openai_adapter.time.sleep"):
                with pytest.raises(OpenAIEmbeddingError) as exc_info:
                    adapter.embed_batch_async(["text"], collection_name="TestFail")

        msg = str(exc_info.value)
        assert "failed" in msg
        assert "sk-te****" in msg
        assert "sk-test-key" not in msg

    def test_expired_status_raises(self, tmp_path, monkeypatch):
        monkeypatch.setattr("embeddings.openai_adapter._BATCH_CHECKPOINT_DIR", tmp_path)

        cfg = EmbeddingConfig(
            type="openai",
            model="text-embedding-3-small",
            api_key="sk-test-key",
            openai_batch=True,
        )

        with patch("embeddings.openai_adapter.openai.OpenAI") as mock_cls:
            mock_client = MagicMock()
            mock_cls.return_value = mock_client
            mock_client.files.create.return_value = _make_file_obj()
            mock_client.batches.create.return_value = _make_batch("validating", id_="b_exp")
            mock_client.batches.retrieve.return_value = _make_batch("expired", id_="b_exp")

            adapter = OpenAIEmbeddingAdapter(cfg)
            with patch("embeddings.openai_adapter.time.sleep"):
                with pytest.raises(OpenAIEmbeddingError) as exc_info:
                    adapter.embed_batch_async(["text"], collection_name="TestExp")

        assert "expired" in str(exc_info.value)

    def test_cancelled_status_raises(self, tmp_path, monkeypatch):
        monkeypatch.setattr("embeddings.openai_adapter._BATCH_CHECKPOINT_DIR", tmp_path)

        cfg = EmbeddingConfig(
            type="openai",
            model="text-embedding-3-small",
            api_key="sk-test-key",
            openai_batch=True,
        )

        with patch("embeddings.openai_adapter.openai.OpenAI") as mock_cls:
            mock_client = MagicMock()
            mock_cls.return_value = mock_client
            mock_client.files.create.return_value = _make_file_obj()
            mock_client.batches.create.return_value = _make_batch("validating", id_="b_cancel")
            mock_client.batches.retrieve.return_value = _make_batch("cancelled", id_="b_cancel")

            adapter = OpenAIEmbeddingAdapter(cfg)
            with patch("embeddings.openai_adapter.time.sleep"):
                with pytest.raises(OpenAIEmbeddingError) as exc_info:
                    adapter.embed_batch_async(["text"], collection_name="TestCancel")

        assert "cancelled" in str(exc_info.value)


# ---------------------------------------------------------------------------
# TestBatchResume
# ---------------------------------------------------------------------------

class TestBatchResume:
    def test_resume_from_existing_checkpoint(self, tmp_path, caplog, monkeypatch):
        monkeypatch.setattr("embeddings.openai_adapter._BATCH_CHECKPOINT_DIR", tmp_path)
        # Pre-write a checkpoint simulating an interrupted run
        _write_batch_checkpoint("Products", "b_existing", "f_existing")

        cfg = EmbeddingConfig(
            type="openai",
            model="text-embedding-3-small",
            api_key="sk-test-key",
            openai_batch=True,
        )
        vectors = [[0.1, 0.2], [0.3, 0.4]]

        with patch("embeddings.openai_adapter.openai.OpenAI") as mock_cls:
            mock_client = MagicMock()
            mock_cls.return_value = mock_client
            mock_client.batches.retrieve.return_value = _make_batch(
                "completed", completed=2, total=2,
                id_="b_existing", output_file_id="f_out_existing"
            )
            mock_client.files.content.return_value = _make_output_content(vectors)

            adapter = OpenAIEmbeddingAdapter(cfg)
            with caplog.at_level(logging.WARNING, logger="embeddings.openai_adapter"):
                with patch("embeddings.openai_adapter.time.sleep"):
                    result = adapter.embed_batch_async(["a", "b"], collection_name="Products")

        # files.create and batches.create must NOT have been called (resumed from checkpoint)
        mock_client.files.create.assert_not_called()
        mock_client.batches.create.assert_not_called()
        # batches.retrieve called with the existing batch_id
        mock_client.batches.retrieve.assert_called_once_with("b_existing")
        assert result == vectors

    def test_resume_when_expired_raises(self, tmp_path, monkeypatch):
        monkeypatch.setattr("embeddings.openai_adapter._BATCH_CHECKPOINT_DIR", tmp_path)
        _write_batch_checkpoint("Products", "b_expired_ck", "f_expired_ck")

        cfg = EmbeddingConfig(
            type="openai",
            model="text-embedding-3-small",
            api_key="sk-test-key",
            openai_batch=True,
        )

        with patch("embeddings.openai_adapter.openai.OpenAI") as mock_cls:
            mock_client = MagicMock()
            mock_cls.return_value = mock_client
            mock_client.batches.retrieve.return_value = _make_batch(
                "expired", id_="b_expired_ck"
            )

            adapter = OpenAIEmbeddingAdapter(cfg)
            with patch("embeddings.openai_adapter.time.sleep"):
                with pytest.raises(OpenAIEmbeddingError) as exc_info:
                    adapter.embed_batch_async(["a", "b"], collection_name="Products")

        # Error message should instruct user to delete checkpoint
        msg = str(exc_info.value)
        assert "checkpoint" in msg.lower() or "delete" in msg.lower() or "expired" in msg.lower()
