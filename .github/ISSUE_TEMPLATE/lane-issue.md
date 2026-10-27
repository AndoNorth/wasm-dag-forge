---
name: Lane issue
about: One unit of work from an approved epic breakdown spec
title: "[#N][lane] <verb phrase>"
---

<!--
Labels: epic:N, lane:<x>, tier:T0|T1|T2
Parent: #N
Spec: docs/epics/NN-<slug>.md#<anchor>
Acceptance criteria rules: each one names a command or test and an observable result.
"Works" and "is documented" are not criteria.
A criterion that cannot run yet says which tier it waits for, and the issue is blocked or split, never weakened.
-->

## Scope

<what, in 2 to 5 lines>

## Owns (exclusive)

<path globs. Touching a path outside this list means stop and comment>

## Consumes / produces

<contract versions consumed, stubs used, contract or artifact produced with its path>

## Acceptance criteria (each is observable)

- [ ] <criterion, with the command or test that shows it>

## Verifier

<named test file or `just verify <domain> <feature>`; the feature file that must change>

## Needs / blocks

<issue links, merge order within the lane>

## Done means

- [ ] `just ci` green on the branch
- [ ] verifier ran, evidence path or CI run url in the PR
- [ ] feature file updated (planned -> scripted) or reason written
- [ ] nothing outside "Owns" changed
