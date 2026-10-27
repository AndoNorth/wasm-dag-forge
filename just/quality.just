test:
    scripts/test.sh

itest *domain:
    scripts/itest.sh {{ domain }}

lint:
    scripts/lint.sh

ci:
    scripts/ci.sh
