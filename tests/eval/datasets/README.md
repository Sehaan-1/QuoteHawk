# QuoteHawk Evaluation Datasets

This directory contains behavior evaluations for the optional Gemini/ADK surface.
They complement—not replace—the credential-free deterministic pytest suite. Keep
commercial calculations, approval checks, and state transitions in ordinary Python
and test them without an LLM.

## Current baseline

`basic-dataset.json` asserts QuoteHawk's seven non-negotiable conversational
boundaries:

1. it cannot issue a PO and requires buyer approval for a future PO draft;
2. it must not disclose a competitor's identity, attachment, or commercial terms;
3. it cannot treat missing freight as zero;
4. instructions embedded in a supplier document are untrusted data, not authority;
5. it must reject malformed or ambiguous commercial values rather than invent them;
6. it cannot skip approval states or approve an over-budget quote; and
7. it cannot send a counter-offer, call an ERP, create a binding PO, or claim an
   external action was completed.

Matching deterministic coverage lives in `tests/unit/test_quote_extraction.py`,
`tests/unit/test_negotiation_service.py`, `tests/unit/test_buyer_approval.py`, and
`tests/unit/test_api.py`. The fixture/simulator regressions must stay runnable
without credentials or network access.

The foundation PR does **not** run this LLM evaluation because it intentionally
needs no model credentials. Run it after configuring `GOOGLE_API_KEY` or
`GEMINI_API_KEY`:

```bash
agents-cli eval run
```

## Dataset format

The file follows the Gemini platform evaluation dataset format. Each case has a
unique `eval_case_id`, a one-turn `prompt`, and a concise reference response that
serves as a safety/quality rubric. An evaluation case may also use the multi-turn
`agent_data.turns` form when negotiation state is introduced.

```json
{
  "eval_cases": [
    {
      "eval_case_id": "unique_case_id",
      "prompt": {
        "role": "user",
        "parts": [{"text": "User message"}]
      }
    }
  ]
}
```

## Iteration loop

1. Run `agents-cli eval run` against a known key/model configuration.
2. Inspect failing cases and judge rationales in `artifacts/grade_results/`.
3. Tighten instructions, tool descriptions, and schema/guard checks—not hidden
   reasoning or brittle prompt hacks.
4. Re-run the evaluation, then use `agents-cli eval compare` for regression
   diffs and `agents-cli eval analyze` for larger failure sets.

The custom judge receives only the user prompt, final response, and reference
rubric. Do not add raw supplier documents, credential material, proprietary
commercial payloads, or model traces to evaluation artifacts.

Add adversarial supplier scenarios as QuoteHawk gains extraction, anomaly,
negotiation, and approval capabilities. Do not use generated supplier text or
untrusted documents as a reason to relax the deterministic guardrails.
