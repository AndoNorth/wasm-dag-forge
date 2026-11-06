test:
    scripts/test.sh

itest *domain:
    scripts/itest.sh {{ domain }}

contracts-map:
    scripts/check-contracts-map.sh

lint:
    scripts/lint.sh

ci:
    scripts/ci.sh

pre-push *check:
    scripts/hooks/pre-push.sh {{ check }}

hooks-status:
    scripts/hooks/status.sh
