"""Job table on Neon Postgres and its state machine. Shared state for every agent.

Build step 2.1: create job; legal transitions pass; illegal ones raise.

States:
    queued -> running -> needs_approval -> approved -> done
    any non-terminal state -> failed
(done and failed are terminal.)

Columns (planned): id, type (coder|email|food|research), details jsonb, state, summary
(short, speakable), deadline timestamptz, result jsonb, created_at, updated_at.

Workers claim jobs with SELECT ... FOR UPDATE SKIP LOCKED so a local Mac worker (food) and
the Railway process can share the table without an agent-to-agent protocol.
"""
