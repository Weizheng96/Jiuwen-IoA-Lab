# Build only after building the ordinary release and collecting its complete
# wheelhouse. BASE_IMAGE must itself be reviewed and pinned by sha256.
ARG BASE_IMAGE
FROM ${BASE_IMAGE}
COPY wheelhouse /opt/wheelhouse
COPY locks /opt/locks
RUN python -m venv /opt/core && python -m venv /opt/participant && python -m venv /opt/swarm \
 && /opt/core/bin/pip install --no-index --find-links=/opt/wheelhouse -r /opt/locks/core.lock \
 && /opt/participant/bin/pip install --no-index --find-links=/opt/wheelhouse -r /opt/locks/participant.lock \
 && /opt/swarm/bin/pip install --no-index --find-links=/opt/wheelhouse -r /opt/locks/swarm.lock \
 && /opt/core/bin/pip install --no-index --find-links=/opt/wheelhouse -c /opt/locks/core.lock ioalab \
 && /opt/participant/bin/pip install --no-index --find-links=/opt/wheelhouse -c /opt/locks/core.lock -c /opt/locks/participant.lock ioalab-trial ioalab-jiuwen ioalab-official \
 && useradd -u 10001 -M core && useradd -u 10002 -M assistant && useradd -u 10003 -M native && useradd -u 10004 -M services
COPY plan.json service-ids.json /opt/trial/
COPY assistant-skill /opt/trial/assistant-skill
# No acceptance receipt is fabricated here. The image remains unready until
# target-machine original-browser acceptance supplies the reviewed receipt.
ENV PATH=/opt/participant/bin:/opt/core/bin:/opt/swarm/bin:/usr/local/bin:/usr/bin:/bin PYTHONDONTWRITEBYTECODE=1
WORKDIR /epoch
ENTRYPOINT ["/opt/participant/bin/python", "-m", "ioalab_trial.agent"]
