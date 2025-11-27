# Architecture Decision Records (ADRs)

This directory contains Architecture Decision Records for the IV Crush 2.0 system.

## What is an ADR?

An Architecture Decision Record (ADR) captures an important architectural decision made along with its context and consequences. ADRs help:
- Document the reasoning behind key decisions
- Provide historical context for future developers
- Enable informed changes (understand trade-offs before modifying)
- Share knowledge across the team

## Format

Each ADR follows this template:
1. **Title**: Brief description of the decision
2. **Status**: Proposed | Accepted | Deprecated | Superseded
3. **Context**: Problem statement and background
4. **Decision**: What we decided to do
5. **Consequences**: Positive, negative, and mitigation strategies
6. **Alternatives Considered**: Other options we evaluated

## Index

### Security & Infrastructure
- [ADR-001: JSON Serialization Over Pickle](./001-json-serialization-over-pickle.md)
  - **Decision**: Use JSON instead of pickle for cache serialization
  - **Rationale**: Eliminate arbitrary code execution risk
  - **Impact**: P0 (Critical) - Security improvement
  - **Status**: Accepted (November 2024)

- [ADR-002: Database Connection Pooling](./002-connection-pooling.md)
  - **Decision**: Implement thread-safe connection pooling
  - **Rationale**: Reduce connection overhead, improve concurrent performance
  - **Impact**: P0 (Critical) - Performance improvement (55% faster scans)
  - **Status**: Accepted (November 2024)

### Code Architecture
- [ADR-004: Extract Strategy Scoring](./004-extract-strategy-scoring.md)
  - **Decision**: Separate scoring logic from strategy generation
  - **Rationale**: Improve testability, maintainability, and separation of concerns
  - **Impact**: P1 (High) - Reduced complexity, improved testability
  - **Status**: Accepted (November 2024)

- [ADR-005: Database Migration System](./005-database-migration-system.md)
  - **Decision**: Implement formal migration system with version tracking
  - **Rationale**: Repeatable, auditable schema changes
  - **Impact**: P1 (High) - Deployment reliability, version control
  - **Status**: Accepted (November 2024)

## Future ADRs

Planned architecture decisions to document:
- ADR-006: VRP Threshold Selection (2.0x / 1.5x / 1.2x)
- ADR-006: Exponentially Weighted Consistency Scoring
- ADR-007: Interpolated vs ATM-Only Implied Move
- ADR-008: Delta-Based vs Distance-Based Strike Selection
- ADR-009: Iron Butterfly POP Estimation Formula

## Creating New ADRs

When making significant architectural decisions:

1. Copy the template from an existing ADR
2. Number sequentially (ADR-004, ADR-005, etc.)
3. Fill in all sections completely
4. Include quantitative data where possible
5. Document alternatives considered
6. Get team review before marking "Accepted"
7. Update this index

## Superseding ADRs

When an ADR becomes obsolete:
1. Mark old ADR as "Superseded by ADR-XXX"
2. Create new ADR explaining the change
3. Reference old ADR in new decision's context
4. Keep old ADR in repo for historical reference

## References

- [ADR Best Practices](https://github.com/joelparkerhenderson/architecture-decision-record)
- Michael Nygard: ["Documenting Architecture Decisions"](https://cognitect.com/blog/2011/11/15/documenting-architecture-decisions)
