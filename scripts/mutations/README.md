# One row per rule

A rule nothing catches is a rule nothing protects. `scripts/mutate.py` turns
each rule off in turn and runs the suite; a rule whose removal breaks no test
has no test. `tests/test_rules_have_evidence.py` counts the rules in the source
against the rows here, so this folder cannot fall behind.

Each row is a file of its own, named after the rule, in a folder that is the
whole path of the file it switches off. Two pull requests then only touch the
same file when they add the same rule. `# RULE: a status is kept for thirty
seconds` in `src/heatpump_mcp/service.py` goes in
`src/heatpump_mcp/service.py/a-status-is-kept-for-thirty-seconds.toml` here:

```toml
[[rule]]
name = "a status is kept for thirty seconds"
file = "src/heatpump_mcp/service.py"
find = 'if self._cached is not None and now - self._cached\[0\] < STATUS_CACHE_SECONDS:'
replace = 'if False:'
```

`find` is a regular expression matching the single line to replace; `replace`
is what replaces it, keeping the file valid. The line's own indentation is
kept, so do not write indentation here. When the pattern matches more than one
line, `occurrence = 2` picks the second.
