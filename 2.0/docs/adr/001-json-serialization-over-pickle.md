# ADR-001: JSON Serialization Over Pickle for Caching

## Status
Accepted (November 2024)

## Context
The hybrid cache (L1 memory + L2 SQLite) originally used Python's `pickle` module for serialization. While pickle is convenient for serializing complex Python objects, it has a critical security vulnerability: **arbitrary code execution during deserialization**.

### Security Risk
Pickle can execute arbitrary Python code when deserializing untrusted data. While our cache only stores internally generated data, this still poses risks:
- Accidental corruption could trigger code execution
- Future codebase changes might introduce external data
- Compliance and security audits flag pickle as high-risk

### Performance Considerations
- Pickle: Fast, binary format, ~20% smaller than JSON
- JSON: Slower serialization, human-readable, widely supported

## Decision
**Replace pickle with JSON serialization for all cache operations.**

Implemented via custom JSON encoder/decoder (`src/utils/serialization.py`) that handles all domain types:
- Value objects: Money, Percentage, Strike
- Complex types: OptionChain, OptionQuote, VRPResult
- Python built-ins: datetime, date, Decimal
- Enums: EarningsTiming, OptionType, Recommendation, etc.

## Consequences

### Positive
✅ **Security**: Eliminates arbitrary code execution risk
✅ **Auditable**: Cache data is human-readable for debugging
✅ **Cross-language**: JSON cache could be read by other tools
✅ **Versioning**: Easier to handle schema migrations
✅ **Compliance**: Meets security audit requirements

### Negative
⚠️ **Performance**: ~15% slower serialization/deserialization
⚠️ **Size**: ~25% larger cache entries (mitigated by L2 cleanup)
⚠️ **Complexity**: Custom encoder/decoder maintenance

### Mitigation
- L1 cache (memory) is unaffected - stores Python objects directly
- L2 cache (SQLite) performance impact is acceptable (<100ms overhead per cache operation)
- Automatic cleanup prevents unbounded cache growth

## Implementation Notes
- All cache writes go through `serialize()` function
- All cache reads go through `deserialize()` function
- Schema versioning via `__type__` field in JSON
- Backward compatibility: Old pickle caches are automatically invalidated on first error

## References
- OWASP: [Deserialization of Untrusted Data](https://owasp.org/www-community/vulnerabilities/Deserialization_of_untrusted_data)
- Python Security: [pickle module documentation](https://docs.python.org/3/library/pickle.html#module-pickle)
- Implementation: `src/utils/serialization.py`
- Migration: `src/infrastructure/cache/hybrid_cache.py`

## Alternatives Considered
1. **MessagePack**: Faster than JSON but still requires trust (rejected)
2. **Protocol Buffers**: Requires schema definition (too heavyweight)
3. **Restricted Pickle**: Custom unpickler with whitelist (still risky)
4. **Keep Pickle**: Accept security risk (rejected due to audit)
