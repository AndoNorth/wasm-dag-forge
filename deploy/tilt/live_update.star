# Live-update library for Python services, applied by a module's Tiltfile.module (see live-update.md).
# The Tiltfile exports the profile's live_update flag as WDF_LIVE_UPDATE, and a module opts in by calling
# python_live_update instead of docker_build.

def python_live_update(ref, context, src, dest, requirements = "requirements.txt", dockerfile = "Dockerfile"):
    """Builds `ref` from `context`, and in a live-update profile syncs `src` to `dest` in the running container.

    A change to `requirements` reinstalls the dependencies inside the container.
    Outside a live-update profile the image is built plain, so the deployed tag is the content-addressed tilt tag.
    """
    kwargs = {}
    if os.getenv("WDF_LIVE_UPDATE") == "1":
        kwargs["live_update"] = [
            sync(context + "/" + requirements, dest + "/" + requirements),
            sync(context + "/" + src, dest + "/" + src),
            run("pip install -r " + dest + "/" + requirements, trigger = [context + "/" + requirements]),
        ]
    docker_build(ref, context, dockerfile = context + "/" + dockerfile, **kwargs)
