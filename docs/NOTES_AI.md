# Notes AI reliability and provider plan

Audit: 22 October 2026. Provider availability and subscription rules must be
checked again before implementing a new integration.

## Current repair

- Both Notes actions shared a preview function that unconditionally imported
  `markdown_it`, absent from the deployed container. The exception happened after
  generation/save, outside the action's exception handler.
- The repair preserves Markdown generation/download and serves escaped text when
  rich rendering is unavailable. It explicitly labels that limitation. Installing
  a rich renderer remains a separate dependency decision.
- Custom prompts now set their HTMX endpoint before processing the form. Non-JSON
  failures produce a visible message instead of a silent JavaScript exception.
- Archived notes render from the archive. Each request uses a separate temporary
  directory, numeric page ordering, and cleanup, avoiding stale/shared page images.
- Settings tests previously ignored `SUPERNOTE_ENV_FILE` when choosing their
  temporary directory. The live model setting was the exact fixture `gemini-new`.
  Tests now isolate the environment file and runtime settings. Run tests in an
  isolated container without live data/config mounts.
- Live Gemini model discovery and a synthetic-image inference succeeded using
  `gemini-3.6-flash`. That model was already available to the existing key; changing
  AI vendors is not required to repair these failures.

## Recommended architecture

1. Add a provider interface for Gemini, Ollama, and optional OpenAI Responses.
   Keep rendering, prompts, persistence, and UI independent of provider clients.
   Expose the actual provider/model used, connection checks, timeout/quota errors,
   and explicit fallback choices. A local request must not silently go to cloud.
2. Move conversion/inference into durable jobs with per-page progress, cancellation,
   bounded retries, resumable work, and a retained failure reason. A SQLite-backed
   worker can fit the current deployment without adding Redis as a prerequisite.
3. Store processing runs separately from the current `ProcessedNote` result:
   input/content hash, prompt/version/hash, provider, resolved model or local model
   digest, renderer version, page results, timestamps, and provider usage. Cache
   only when those inputs match. Preserve prior custom-prompt results.
4. Separate faithful transcription from optional rewriting/formatting. Mark
   uncertain handwriting rather than inventing content. Validate table/Mermaid
   output and create real figure assets before emitting Obsidian asset links.
5. Keep representative Danish/English handwriting, tables, diagrams, and long
   notes as an approved evaluation set. Test provider upgrades against it before
   changing defaults. Add synthetic endpoint smoke checks to deployment.
6. Treat model lifecycle, billing/quota, container dependencies, and preview
   rendering as separate health checks. Avoid guessing future model identifiers.

## Ollama integration: active local provider

The app container can reach `http://rockpigeon:11434`. Ollama reports version
`0.32.9`. The host has an NVIDIA RTX 3050 with 6 GB VRAM.

The installed default is **`qwen3-vl:4b-instruct-q4_K_M`**, a quantized 4B vision
model. Tested digest:
`84f7ed8042fa7cdbd902f7dcb7d0370e032095047cc3244179f263b77b5bc318`.
With context 8192, `/api/ps` reports 4,236,697,927 bytes resident, all in VRAM.
The host's total GPU usage was 5186 MiB of 6144 MiB during the check. A printed
four-line note took 5.64 seconds including 3.62 seconds loading and preserved its
words/numbers. A real handwritten page took 5.83 seconds warm. Visual comparison
found useful transcription but misread several words and spatial relationships;
users must review results. These are spot checks, not an accuracy evaluation.
The earlier 9.7B `qwen3.5:latest` benchmark took 34.8 seconds cold; the smaller
dedicated vision model leaves more room on this GPU.

Both **Process with AI** and **custom prompts** now route through `AI_PROVIDER`.
Settings → AI allows choosing Ollama or Gemini, changing the local address/model,
and checking that an installed model supports vision. The check verifies service
and metadata availability; a real processing request is the inference check.
No Gemini key is required for local processing. Gemini remains an explicit option
using the existing key; Ollama failures never send notes to it automatically.

Deployment configuration in `/config/app.env`:

```dotenv
AI_PROVIDER=ollama
OLLAMA_BASE_URL=http://rockpigeon:11434
OLLAMA_MODEL=qwen3-vl:4b-instruct-q4_K_M
OLLAMA_TIMEOUT=180
```

No new Python packages are required. The model was downloaded through the existing
Ollama service. Installed-model metadata is checked before image upload; cloud
aliases and nonvision models are rejected. Images stay on the private network.
The exact model tag is configured, but replacement of that tag is not yet blocked
by a digest pin; run-history/digest enforcement remains future work.

Requests process 1–12 pages sequentially with a maximum image dimension of 1600,
8K context, 3072 output tokens per inference, and a 15-minute total deadline.
A nonblocking filesystem lock permits one Notes inference job across processes;
other requests receive a busy message. This is not a durable queue. External
Ollama clients do not share this lock. Keep rockpigeon online while processing.
Each HTTP request has a bounded timeout. Missing models, timeouts, truncated,
incomplete, or empty responses preserve previous Markdown.

For multi-page custom prompts, pages are first transcribed individually and the
custom instruction is applied once to the combined text. Conservative UTF-8 byte
bounds prevent silent context truncation: long prompts/combined transcriptions
are rejected with an instruction to split the note or explicitly select Gemini.
Plain conversion joins page transcriptions in numeric page order. Figure asset
extraction is not implemented; local prompts forbid invented asset links.

References: [model card](https://ollama.com/library/qwen3-vl:4b-instruct-q4_K_M),
[Ollama vision API](https://docs.ollama.com/capabilities/vision).

## ChatGPT/Codex subscription and OpenAI

Standard Platform API keys are a separate usage-based option. A subscription
does not automatically authorize this Django app to make API requests.

Current official documentation also offers **Sign in with ChatGPT plan usage**
for eligible open-source/personal tools, with guidance for self-hosted VMs.
This is a possible subscription-backed provider, subject to account/workspace
eligibility and application hosting rules. It needs its own consented OAuth
integration, protected token refresh/storage, and stable host identity; do not
extract or repurpose the coding assistant's existing credentials.

The documented preview accepts image inputs when the selected model supports
them. It requires streaming Responses requests with `store: false`, consumes
the user's shared plan limits, and has different parameter/tool restrictions
from ordinary API-key requests. Treat incomplete streams as failures, and
discover the signed-in account's available models instead of hardcoding access.

References: [plan usage](https://developers.openai.com/siwc/token-sharing-open-source),
[self-hosted VMs](https://developers.openai.com/siwc/token-sharing-open-source/self-hosted-vms),
[preview limitations](https://developers.openai.com/siwc/token-sharing-open-source/preview-limitations),
[API-key versus subscription authentication](https://learn.chatgpt.com/docs/auth).

## Delivery order

1. Deploy and verify the existing Gemini repairs and restore its valid setting.
2. Add provider selection and Ollama support using the existing HTTP stack; run
   a real handwriting comparison before changing the default.
3. Add persistent jobs/run history and provider-independent regression checks.
4. Evaluate the supported ChatGPT OAuth integration, or a conventional OpenAI API
   key, as a deliberate optional cloud provider. Keep provider switching reversible.
