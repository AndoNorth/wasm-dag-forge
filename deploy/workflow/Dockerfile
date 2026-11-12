# The workflow worker service. The context is the one image.sh prepares: requirements.txt, the hash-pinned
# third-party packages, and wheels/, the workspace packages built from the tree.
ARG BASE
FROM ${BASE}
ARG WDF_BUILD_ID
COPY requirements.txt /tmp/build/
RUN pip install --no-cache-dir --no-deps --require-hashes -r /tmp/build/requirements.txt
COPY wheels /tmp/build/wheels
RUN pip install --no-cache-dir --no-deps --no-index /tmp/build/wheels/*.whl && pip check && rm -rf /tmp/build
ENV WDF_BUILD_ID=${WDF_BUILD_ID} PYTHONUNBUFFERED=1
USER 65532:65532
ENTRYPOINT ["python", "-m", "wdf_workflow.service"]
