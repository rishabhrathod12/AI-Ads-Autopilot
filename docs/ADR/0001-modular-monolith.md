# ADR 0001: Modular monolith

Status: Accepted (owner approved M0, 2026-11-02)

## Context
MVP needs speed and simplicity; team is one person.

## Decision
Single FastAPI deployable with strict layer/module boundaries (domain, application, infrastructure, api).

## Consequences
Simple deploy and debugging; modules can be extracted later. Requires discipline to enforce boundaries (import-linter in CI).
