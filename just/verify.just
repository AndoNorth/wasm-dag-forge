verify domain *features:
    scripts/verify/{{ domain }}/run.sh {{ features }}

verify-lint:
    scripts/verify-lint.sh
