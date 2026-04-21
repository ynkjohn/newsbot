# Task proposals from codebase review

## 1) Typo fix task
**Issue found:** In the question keyword list, there is likely a typo: `"me disses"` (probably intended to be `"me disse"` or `"me diz"`). This can reduce keyword matching quality for short natural-language prompts.  
**Where:** `interactions/command_router.py` (`_is_valid_question`).

**Proposed task:**
- Replace `"me disses"` with the intended phrase (`"me disse"` or remove it if redundant).
- Add a focused unit test that validates the intended phrase is recognized by the classifier logic (if/when keyword logic is enforced for short inputs).

**Acceptance criteria:**
- No obvious typos remain in `news_keywords`.
- A regression test covers the corrected phrase.

---

## 2) Bug fix task
**Issue found:** `_is_valid_question` documents keyword-aware behavior, but in practice it returns `True` for any text with 3+ words, regardless of topic. This over-classifies arbitrary chatter as a "question" and can trigger unnecessary LLM responses.  
**Where:** `interactions/command_router.py` (`_is_valid_question`).

**Proposed task:**
- Align implementation with intended behavior:
  - keep rejecting 1–2 word messages unless they match explicit question/news patterns;
  - for 3+ word messages, require either interrogative form (`?`, interrogative starters) or news-intent keywords.
- Add/adjust tests to ensure non-news 3+ word chatter in groups is ignored.

**Acceptance criteria:**
- A 3+ word casual phrase (e.g., "vamos jogar bola hoje") is classified as `("other", None)`.
- A genuine news question remains classified as `("question", None)`.

---

## 3) Code comment / documentation discrepancy task
**Issue found:** The test name and docstring indicate article-processing behavior, but the test only validates `SummaryOutput` schema fields.  
**Where:** `tests/test_summarizer.py` (`test_summarizer_marks_articles_processed`).

**Proposed task:**
- Either:
  1) rename the test + docstring to reflect what it actually verifies, **or**
  2) extend it to actually call `generate_summaries_for_category` and assert article `processed` flags are updated.

**Acceptance criteria:**
- Test name/docstring accurately describe assertions.
- No misleading test intent remains in this file.

---

## 4) Test improvement task
**Issue found:** `tests/test_whatsapp_sender.py` has assertions that are too weak to protect behavior (e.g., `assert mock_post.call_count >= 1` in retry test), so regressions in retry logic may pass unnoticed.  
**Where:** `tests/test_whatsapp_sender.py`.

**Proposed task:**
- Strengthen retry tests to assert exact retry counts and outcomes:
  - 5xx errors retry up to max attempts and then fail;
  - success-after-retry path asserts exact number of attempts;
  - 4xx path asserts no retry.
- Mock `time.sleep` to avoid slowing tests and to verify backoff intervals.

**Acceptance criteria:**
- Retry tests fail if retry counts/backoff behavior changes unexpectedly.
- Test suite runtime does not significantly increase.
