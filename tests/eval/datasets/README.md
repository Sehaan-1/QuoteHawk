# QuoteHawk Evaluation Datasets

This directory contains behavior evaluations for the optional Gemini/ADK surface.
They complement—not replace—the credential-free deterministic pytest suite. Keep
commercial calculations, approval checks, and state transitions in ordinary Python
and test them without an LLM.

## Current baseline

`basic-dataset.json` asserts QuoteHawk's three non-negotiable conversational
boundaries:

1. it cannot issue a PO and requires buyer approval for a future PO draft;
2. it must not disclose a competitor's identity or commercial terms; and
3. it cannot treat missing freight as zero.

The foundation PR does **not** run this LLM evaluation because it has no
extraction/negotiation adapter and intentionally needs no model credentials. Run
it from PR 3 onward after configuring `GOOGLE_API_KEY` or `GEMINI_API_KEY`:

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

Add adversarial supplier scenarios as QuoteHawk gains extraction, anomaly,
negotiation, and approval capabilities. Do not use generated supplier text or
untrusted documents as a reason to relax the deterministic guardrails.
