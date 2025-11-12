# Profiling & Benchmarking Quick Reference

Quick commands and workflows for performance analysis.

---

## 🚀 Quick Start

### Profile Current Code (Data Fetching Only)
```bash
# Basic profiling
python -m cProfile -o profiling/results/profile.prof -m src.analysis.earnings_analyzer \
    --tickers "AAPL,MSFT,GOOGL" 2025-11-15 --yes

# Analyze results
python profiling/profiler.py --analyze profiling/results/profile.prof --top 30
```

### Benchmark Performance
```bash
# Run benchmark with profiling
python benchmarks/performance_tracker.py \
    --tickers "AAPL,MSFT,GOOGL" --compare --profile
```

### Comprehensive Analysis
```bash
python profiling/comprehensive_profile.py
```

---

## 📊 Common Workflows

### Workflow 1: Establish Baseline

```bash
# 1. Create baseline benchmark
python benchmarks/performance_tracker.py \
    --tickers "AAPL,MSFT,GOOGL" --baseline --profile

# 2. View baseline
python benchmarks/performance_tracker.py --history
```

### Workflow 2: Test Optimization

```bash
# 1. Make code changes
# ... edit src/analysis/ticker_data_fetcher.py ...

# 2. Run comparison benchmark
python benchmarks/performance_tracker.py \
    --tickers "AAPL,MSFT,GOOGL" --compare --profile

# 3. Check for improvement/regression
# Output will show % improvement vs baseline
```

### Workflow 3: Find Bottlenecks

```bash
# 1. Profile with detailed output
python -m cProfile -o profiling/results/hotspot.prof \
    -m src.analysis.earnings_analyzer --tickers "AAPL,MSFT" --yes

# 2. Find hotspots (functions > 0.1s)
python profiling/profiler.py --hotspots profiling/results/hotspot.prof

# 3. Analyze top functions
python profiling/profiler.py --analyze profiling/results/hotspot.prof --top 40
```

### Workflow 4: Visual Analysis

```bash
# 1. Install snakeviz
pip install snakeviz

# 2. Profile
python -m cProfile -o profile.prof \
    -m src.analysis.earnings_analyzer --tickers "AAPL" --yes

# 3. Visualize (opens browser)
snakeviz profile.prof
```

---

## 🔧 Tool Commands

### profiler.py

```bash
# Analyze profile file
python profiling/profiler.py --analyze <file.prof> --top 30

# Find hotspots (functions > 0.1s)
python profiling/profiler.py --hotspots <file.prof>

# Compare two profiles
python profiling/profiler.py --compare baseline.prof new.prof

# Run example
python profiling/profiler.py --example
```

### performance_tracker.py

```bash
# Create baseline
python benchmarks/performance_tracker.py \
    --tickers "AAPL,MSFT,GOOGL" \
    --date 2025-11-15 \
    --baseline \
    --profile

# Run benchmark and compare
python benchmarks/performance_tracker.py \
    --tickers "AAPL,MSFT,GOOGL" \
    --date 2025-11-15 \
    --compare \
    --profile

# View history
python benchmarks/performance_tracker.py --history
```

### comprehensive_profile.py

```bash
# Run all profiling analyses
python profiling/comprehensive_profile.py

# Results saved to:
# - profiling/results/comprehensive_profile_*.json
```

---

## 📈 Key Metrics to Track

### Performance Metrics

| Metric | Good | Acceptable | Needs Work |
|--------|------|------------|------------|
| Time per ticker (data fetch) | <0.3s | 0.3-0.5s | >0.5s |
| Time per ticker (full analysis) | <4s | 4-6s | >6s |
| Memory growth | <20MB | 20-50MB | >50MB |
| Cache hit rate | >70% | 50-70% | <50% |

### Network Metrics

| API | Good Latency | Acceptable | Slow |
|-----|--------------|------------|------|
| yfinance .info | <200ms | 200-400ms | >400ms |
| Tradier quote | <100ms | 100-200ms | >200ms |
| Tradier options | <150ms | 150-300ms | >300ms |

---

## 🎯 Profiling Best Practices

### 1. Use Representative Data
```bash
# ✅ Good: Multiple tickers, realistic scenario
--tickers "AAPL,MSFT,GOOGL,NVDA,TSLA"

# ❌ Bad: Single ticker (not representative)
--tickers "AAPL"
```

### 2. Run Multiple Times
```bash
# Run 3 times and average
for i in {1..3}; do
    python benchmarks/performance_tracker.py \
        --tickers "AAPL,MSFT,GOOGL" --compare
done
```

### 3. Profile Production Paths
```bash
# ✅ Profile the actual user workflow
python -m cProfile -o profile.prof \
    -m src.analysis.earnings_analyzer 2025-11-15 10 --yes

# ❌ Don't profile with --override (not realistic)
```

### 4. Check Network Conditions
```bash
# Test with cold cache
rm -rf data/cache/*
python benchmarks/performance_tracker.py --tickers "AAPL,MSFT" --compare

# Test with warm cache (second run)
python benchmarks/performance_tracker.py --tickers "AAPL,MSFT" --compare
```

---

## 🔍 Interpreting Results

### cProfile Output

```
ncalls  tottime  percall  cumtime  percall filename:lineno(function)
    10    0.741    0.074    0.742    0.074 curl_easy_perform
```

- **ncalls:** Number of calls
- **tottime:** Total time in function (excluding subcalls)
- **cumtime:** Cumulative time (including subcalls) ⭐ **Most important**
- **percall:** Time per call

**Focus on:** High cumtime + high ncalls = optimization target

### Benchmark Comparison

```
📈 COMPARISON vs baseline_20251111_145820.json:
   Time: 41.9% improvement
   Memory: -15.2% improvement (used more memory)
```

- **Positive %:** Improvement (faster/less memory)
- **Negative %:** Regression (slower/more memory)
- **>5% change:** Significant, worth investigating

### Hotspots

```
🔥 FINDING HOTSPOTS (functions > 0.1s)
 1. curl_easy_perform       (0.74s, 10 calls)  ← Network I/O
 2. yfinance.info           (0.64s, 3 calls)   ← API call
 3. IVHistoryTracker.__init__(0.48s, 3 calls)   ← DB connection
```

**Action:** Optimize in order (highest cumtime first)

---

## 🐛 Troubleshooting

### Profile file too large
```bash
# Reduce output with filters
python -m cProfile -o profile.prof \
    -s cumulative \  # Sort by cumulative time
    -m src.analysis.earnings_analyzer --tickers "AAPL" --yes

# Then analyze top 20 only
python profiling/profiler.py --analyze profile.prof --top 20
```

### Benchmark shows inconsistent results
```bash
# Possible causes:
# 1. Network latency variance (yfinance/Tradier)
# 2. Cold vs warm cache
# 3. System load (other processes)

# Solution: Run multiple times
for i in {1..5}; do
    python benchmarks/performance_tracker.py --tickers "AAPL,MSFT" --compare
    sleep 5
done
```

### Profiling overhead affects results
```bash
# Use sampling profiler instead (lower overhead)
pip install py-spy
py-spy record -o profile.svg -- python -m src.analysis.earnings_analyzer --tickers "AAPL" --yes

# Or use line_profiler for specific functions only
```

---

## 📚 Additional Resources

### Install Advanced Tools

```bash
# Line-by-line profiling
pip install line_profiler

# Memory profiling
pip install memory_profiler

# Visual profiling
pip install snakeviz

# Sampling profiler (low overhead)
pip install py-spy
```

### Usage Examples

```bash
# Line profiler
# 1. Add @profile decorator to function
# 2. Run with kernprof
kernprof -l -v src/analysis/ticker_data_fetcher.py

# Memory profiler
python -m memory_profiler src/analysis/earnings_analyzer.py

# py-spy (sampling, low overhead)
py-spy record -o profile.svg -- python -m src.analysis.earnings_analyzer --tickers "AAPL" --yes
py-spy top -- python -m src.analysis.earnings_analyzer --tickers "AAPL" --yes
```

---

## 🎓 Performance Debugging Checklist

When investigating performance issues:

- [ ] Profile with cProfile to find hotspots
- [ ] Check network latency (yfinance, Tradier)
- [ ] Verify cache is working (check hit rate)
- [ ] Look for repeated work (same API calls)
- [ ] Check for N+1 queries (DB/API)
- [ ] Measure with/without parallel execution
- [ ] Compare baseline vs current
- [ ] Test with different ticker counts (1, 3, 5, 10)
- [ ] Monitor memory growth
- [ ] Check for resource leaks (DB connections, HTTP sessions)

---

## 💾 Backup Profiling Data

```bash
# Create tarball of all profiling data
tar -czf profiling_backup_$(date +%Y%m%d).tar.gz \
    profiling/results/ \
    benchmarks/results/

# Upload to secure location
# aws s3 cp profiling_backup_*.tar.gz s3://my-bucket/profiling/
```

---

## 📞 Quick Help

```bash
# Profiler help
python profiling/profiler.py --help

# Benchmark help
python benchmarks/performance_tracker.py --help

# Comprehensive profiler
python profiling/comprehensive_profile.py --help
```

---

**Last Updated:** 2025-11-11
**Maintainer:** Development Team
