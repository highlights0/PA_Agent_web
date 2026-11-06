from pathlib import Path

from app.services.crypto_prompt_assembler import CryptoPromptAssembler


def _assembler(tmp_path: Path) -> CryptoPromptAssembler:
    return CryptoPromptAssembler(
        prompt_dir=tmp_path,
        experience_reader=None,
        prompt_settings=None,
        crypto_context_text="CRYPTO_CONTEXT_SENTINEL",
    )


def test_append_crypto_context_adds_to_last_user_message(tmp_path: Path):
    assembler = _assembler(tmp_path)
    messages = [
        {"role": "system", "content": "system"},
        {"role": "user", "content": "stage1 user"},
        {"role": "assistant", "content": "stage1 json"},
        {"role": "user", "content": "stage2 user"},
    ]
    result = assembler._append_crypto_context(messages)
    assert result[0]["content"] == "system"
    assert result[1]["content"] == "stage1 user"
    assert result[3]["content"].endswith("CRYPTO_CONTEXT_SENTINEL")
    assert messages[3]["content"] == "stage2 user"


def test_append_crypto_context_does_not_duplicate_chained_context(tmp_path: Path):
    assembler = _assembler(tmp_path)
    messages = [
        {"role": "system", "content": "system"},
        {"role": "user", "content": "stage1 user\n\nCRYPTO_CONTEXT_SENTINEL"},
        {"role": "assistant", "content": "stage1 json"},
        {"role": "user", "content": "stage2 user"},
    ]
    result = assembler._append_crypto_context(messages)
    assert result[3]["content"] == "stage2 user"
