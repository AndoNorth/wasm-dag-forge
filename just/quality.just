test:
    scripts/test.sh

itest *domain:
    scripts/itest.sh {{ domain }}

contracts-map:
    scripts/check-contracts-map.sh

lint:
    scripts/lint.sh

ci *mode:
    scripts/ci.sh {{ mode }}

pre-push *check:
    scripts/hooks/pre-push.sh {{ check }}

hooks-status:
    scripts/hooks/status.sh
