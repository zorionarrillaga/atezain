# There is no model call in this adapter.

The `invoices-es` adapter has a `prompt.md` because a model reads the record and answers with
proposals. Here the assistant is the Claude Code session the owner is already in: it drafts the
letter as a file, runs the gates, and proposes the send through `bin/atezain_cli.py`. Nothing in
this repo sends a prompt to a model on this adapter's behalf.

This file exists so that a reader who finds `adapters/*/prompt.md` everywhere else does not go
looking for the missing one, and so that `PolicyConfig.load` has the same directory shape either
way. If a model is ever put in front of these drafts, its system prompt goes here and this
paragraph goes away.
