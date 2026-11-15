# Instructions for coding agents

- Read SPEC.md first. It defines every interface between the hub, the UI, and the Swift sidecar.
- Ownership: Kyle owns `hub/`, `ui/`, `prompts/`. Partner owns `sidecar/`. Only edit the other person's folder if asked.
- If a change touches an interface (endpoint, JSON shape, gesture name), update SPEC.md in the same commit.
- Keep it hackathon-simple: no new frameworks, no build step for the UI, no database.
- Everything must run offline on localhost. Never add cloud API calls.
- The only LLM allowed is Bonsai 2 (2B/4B/9B) via the local OpenAI-compatible server. Speech must use Argmax (WhisperKit / TTSKit).
- Prompts live in `prompts/*.txt` so they can be tuned without touching code.
- Run `./test.sh` before committing (a few seconds, no model or hardware). `./test.sh --all` also runs the real sidecar on a recording.
- Prompt files have a fixed part above a line of `=====` and a changing part below it. Anything that varies between calls (conversation, screen, names) goes below, or llama-server's cache is lost.
- Never send a real message or play sound while testing: use `MOCK_SIDECAR=1` (pretend Mac) and `NOD_SILENT=1`.
