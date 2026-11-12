"""The `load_plan` control activity: the bound plan of a job, read from its manifest."""

from functools import cache

import yaml
from temporalio import activity
from wdf_artifactclient.client import ArtifactClient, TooLarge
from wdf_artifactclient.errors import ArtifactError
from wdf_jobdoc.profile import LimitsFile
from wdf_jobstate.models import CONTRACT as JOBSTATE_CONTRACT
from wdf_plan import bind, plan_hash, read_plan

from ..testing.control import LoadedPlan, LoadPlan, control_error


@cache
def limits() -> LimitsFile:
    return LimitsFile.model_validate(
        yaml.safe_load((JOBSTATE_CONTRACT / "limits.yaml").read_text())
    )


def _corrupt(request: LoadPlan, why: str):
    return control_error("ARTIFACT_CORRUPT", f"{request.manifest.uri}: {why}")


class PlanLoader:
    """Reads manifests with a store client of the `workflow` role."""

    def __init__(self, store: ArtifactClient):
        self.store = store

    @activity.defn(name="load_plan")
    def load_plan(self, request: LoadPlan) -> LoadedPlan:
        """The manifest's plan bound to the job's params, with the history limit of its profile.

        A manifest that is not the canonical bytes of its hash, not a readable plan, or not
        bindable to the params can never load, so each is a non-retryable ARTIFACT_CORRUPT.
        """
        # The plan names its profile only once it is read, so the read is bounded by the largest.
        largest = max(profile.max_plan_bytes for profile in limits().profiles.values())
        try:
            body = self.store.get_bytes(request.manifest, largest)
        except ArtifactError as error:
            raise control_error(error.code, str(error)) from error
        except TooLarge as error:
            raise _corrupt(request, str(error)) from error
        try:
            template = read_plan(body)
        except ValueError as error:
            raise _corrupt(request, f"not a readable plan: {error}") from error
        if plan_hash(template) != request.manifest.sha256:
            raise _corrupt(request, f"its canonical plan hashes to {plan_hash(template)}")
        profile = limits().profiles.get(template.profile)
        if profile is None:
            raise _corrupt(request, f"names the unknown limits profile {template.profile}")
        try:
            plan = bind(template, request.params.model_dump(), profile)
        except ValueError as error:
            raise _corrupt(request, f"the job params do not bind: {error}") from error
        return LoadedPlan(plan=plan, max_history_events=profile.max_history_events)
