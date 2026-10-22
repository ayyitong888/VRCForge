from context_compaction import compact_context


def test_verified_large_context_window_plans_all_pages_within_provider_budget():
    history = [
        {"role": "user" if index % 2 == 0 else "assistant", "text": f"entry-{index} " + "x" * 1800}
        for index in range(452)
    ]
    prompts = []
    result = compact_context(
        history,
        target_tokens=250_000,
        real_context_limit=500_000,
        summarizer=lambda prompt: prompts.append(prompt) or "safe historical summary",
    )

    assert result["requestBudgetTokens"] > 64_000
    assert result["coverage"]["complete"] is True
    assert result["completeness"]["summarizerInputComplete"] is True
    assert 1 <= len(prompts) <= 3


def test_provider_call_budget_covers_all_pages_beyond_three_calls():
    history = [
        {"role": "user", "text": f"entry-{index} " + "x" * 1800}
        for index in range(100)
    ]
    prompts = []
    result = compact_context(
        history,
        target_tokens=10_000,
        real_context_limit=20_000,
        summarizer=lambda prompt: prompts.append(prompt) or "safe historical summary",
    )

    assert result["coverage"]["pageCount"] > 3
    assert result["coverage"]["complete"] is True
    assert result["completeness"]["summarizerInputComplete"] is True
    assert result["providerAttempts"] == result["coverage"]["pageCount"]


def test_provider_retry_budget_resets_for_each_page():
    history = [
        {"role": "user", "text": f"entry-{index} " + "x" * 1800}
        for index in range(100)
    ]
    prompts = []
    attempts_by_page = {}

    def summarize(prompt):
        page_index = len(prompts) // 2
        attempts_by_page[page_index] = attempts_by_page.get(page_index, 0) + 1
        prompts.append(prompt)
        if attempts_by_page[page_index] == 1:
            raise TimeoutError("temporary provider timeout")
        return "safe historical summary"

    result = compact_context(
        history,
        target_tokens=10_000,
        real_context_limit=20_000,
        summarizer=summarize,
        sleep=lambda _: None,
    )

    assert result["coverage"]["pageCount"] > 3
    assert result["coverage"]["complete"] is True
    assert all(attempts == 2 for attempts in attempts_by_page.values())
    assert result["providerAttempts"] == result["coverage"]["pageCount"] * 2
