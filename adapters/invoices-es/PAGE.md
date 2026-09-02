# atezain for overdue invoices — one page for the prospect

> **Scaffold (step 5, `PLAN.md` §6).** The structure and every factual line are the builder's; the
> two blanks marked ✋ are the owner's — the URL once the thing is deployed, and the price of an
> adaptation. The language of the page a prospect actually reads is his call too: this scaffold is
> in English because the repo is.

## What it does

You upload a spreadsheet of your invoices — id, customer, amount, currency, issue and due dates,
status; notes and emails as extra rows if you have them. For any invoice you point at, the
assistant gives you three things in Spanish: a summary of where the invoice stands, the next
action it recommends, and the draft of the message to the customer.

It may *propose* three writes, and only three: send the reminder (by email, held for you to
approve), set the status (to one of three values, held for you to approve), or add an internal
note (written at once, audited). Every proposal is checked against a permission table that lives
outside the model before it is even shown to you, and every write that happens is compared
afterwards with what you approved.

## What it refuses

- **Changing an amount.** Never, whoever asks — a customer's email, a colleague's note, or the
  assistant's own reasoning. An amount is money.
- **Deleting an invoice.** Never.
- **Sending anything to an address or channel the table does not know.** Reminders go by email or
  not at all; a note that says "send it to this WhatsApp instead" is information about the note's
  author, not an instruction.
- **Setting a status the table does not list**, such as `paid` or `cancelled`. Those are yours to
  set from the bank statement, not the assistant's to set from a message.
- **More writes than the table allows in a day.** The limit is in the table (`daily_writes`), and
  reaching it trips a fuse that the assistant cannot clear and that nobody can clear the same day.
- **Anything, when it cannot see its own state.** If the policy store cannot be reached, the
  answer is no, never yes.

The table is a text file, and the adaptation to your records is mostly that file: which record
types, which verbs, which fields, which values, which limits, which writes need your hand.

## The numbers on its own seed

A named model on a named date was shown a hundred injections planted in the records the assistant
reads — notes, emails, subjects, a customer name, the text of an attachment — each aiming at one
write the table forbids. `NUMBERS.md` in the repository has the whole table; `WRITEUP.md` reads it.

<!-- numbers:begin -->
_Pasted by `make numbers` from the rows behind `NUMBERS.md`; do not edit by hand. Model `openai/gpt-oss-120b` · run date(s) 2026-09-02 · N = 100 cases · temperature 0.0 · adapter `invoices-es` · prompt sha256[:16] `6dd2c9d49e5d93c0` · Wilson score intervals, 95 %, z = 1.96. `NUMBERS.md` has the per-class, per-technique and per-goal tables._

| all cases | N | manipulated | executed OFF | executed ON |
|---|---|---|---|---|
| openai/gpt-oss-120b | 100 | 1/100 = 1% [0%, 5%] | 1/100 = 1% [0%, 5%] | 0/100 = 0% [0%, 4%] |

| the goal is | N | manipulated | executed OFF | executed ON |
|---|---|---|---|---|
| permitted verb, forbidden value | 30 | 1/30 = 3% [1%, 17%] | 1/30 = 3% [1%, 17%] | 0/30 = 0% [0%, 11%] |
| verb not offered | 70 | 0/70 = 0% [0%, 5%] | 0/70 = 0% [0%, 5%] | 0/70 = 0% [0%, 5%] |

The policy refused every goal proposal in 100/100 = 100% [96%, 100%] of cases; the audit chain verified with no
anomaly in 100/100.

| all cases | N | labelled | goal in prose | manipulated (proposals) |
|---|---|---|---|---|
| openai/gpt-oss-120b | 100 | 100 | 66/100 = 66% [56%, 75%] | 1/100 = 1% [0%, 5%] |

| the goal is | N | labelled | goal in prose | manipulated (proposals) |
|---|---|---|---|---|
| permitted verb, forbidden value | 30 | 30 | 22/30 = 73% [56%, 86%] | 1/30 = 3% [1%, 17%] |
| verb not offered | 70 | 70 | 44/70 = 63% [51%, 73%] | 0/70 = 0% [0%, 5%] |

Where the adopting sentence was read: recommendation 35 · draft 18 · note 13.
<!-- numbers:end -->

Read the last table as much as the two before it: the model proposed a forbidden write once in a
hundred, and adopted the injected goal *in words* — in its recommendation to you, its draft to
your customer, or a note it wrote — in two of three. The layer stops the write. You read the
words. Nothing here reads them for you.

## How to try it

✋ **URL:** not yet — nothing is deployed. Until it is, it runs on one machine with one command:

```
git clone <the repository> && cd atezain
python3 -m venv .venv && .venv/bin/pip install -q pytest langgraph langgraph-checkpoint-sqlite fastapi uvicorn python-multipart openpyxl
make serve                         # then open http://127.0.0.1:8000/demo
```

That is SQLite and a stub model, with no key and no network. To put a real model behind it, set
`GROQ_API_KEY` in the environment and `ATEZAIN_MODEL=groq make serve`.

## How the adaptation is priced

✋ **The owner's number.** Not written here until he writes it. What an adaptation consists of is
above: the permission table for your record types, the prompt in your language and register, the
seed that stands in for your data while it is tested, and the red-team run over that adapter, with
its numbers, before you rely on it.
