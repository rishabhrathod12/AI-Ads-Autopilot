# ADR 0006: LangGraph orchestration with a validator node

Status: Accepted (owner approved M0, 2026-11-02)

## Context
Need typed state, checkpointing and human-in-the-loop interrupts.

## Decision
Use LangGraph with Supervisor + 6 specialists + code Validator; start with a single assistant in M4 and graph in M5.

## Consequences
Traceable, resumable runs; added framework dependency.
