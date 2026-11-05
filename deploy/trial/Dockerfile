# Build only after building the ordinary release and collecting its complete
# wheelhouse. BASE_IMAGE must itself be reviewed and pinned by sha256.
ARG BASE_IMAGE
FROM ${BASE_IMAGE}
ARG TRIAL_RESOURCE_LAYOUT=epoch-ingress/1
LABEL ioalab.trial.resource_layout=${TRIAL_RESOURCE_LAYOUT} ioalab.trial.ingress=fixed-seat-tcp-8080-8767
COPY wheelhouse /opt/wheelhouse
COPY locks /opt/locks
COPY vendor /opt/trial-build/vendor
COPY install_swarm_wheels.py /opt/trial-build/install_swarm_wheels.py
RUN python -m venv /opt/core && python -m venv /opt/participant && python -m venv /opt/swarm \
 && /opt/core/bin/pip install --no-index --find-links=/opt/wheelhouse -r /opt/locks/core.lock \
 && /opt/participant/bin/pip install --no-index --find-links=/opt/wheelhouse -r /opt/locks/participant.lock \
 && /opt/swarm/bin/pip install --no-index --find-links=/opt/wheelhouse -r /opt/locks/swarm.lock \
 && python /opt/trial-build/install_swarm_wheels.py --root /opt/trial-build --python /opt/swarm/bin/python --constraints /opt/locks/swarm.lock \
 && /opt/core/bin/pip install --no-index --find-links=/opt/wheelhouse -c /opt/locks/core.lock ioalab \
 && mkdir -p /opt/trial \
 && /opt/core/bin/python -c 'import json; from ioalab.configuration import defaults; print(json.dumps(defaults()))' > /opt/trial/platform-template.json \
 && /opt/participant/bin/pip install --no-index --find-links=/opt/wheelhouse -c /opt/locks/core.lock -c /opt/locks/participant.lock ioalab-trial ioalab-jiuwen ioalab-official \
 && /opt/participant/bin/python -c 'import sys; from ioalab_trial.profile import RESOURCE_LAYOUT, INGRESS_CAPABILITY; assert RESOURCE_LAYOUT == sys.argv[1] and INGRESS_CAPABILITY == "fixed-seat-tcp-8080-8767"' "$TRIAL_RESOURCE_LAYOUT" \
 && useradd -u 10001 -M core && useradd -u 10002 -M assistant && useradd -u 10003 -M native && useradd -u 10004 -M services
COPY plan.json service-ids.json /opt/trial/
COPY assistant-skill /opt/trial/assistant-skill
COPY acceptance /opt/trial/acceptance
# No acceptance receipt is fabricated here. The image remains unready until
# target-machine original-browser acceptance supplies the reviewed receipt.
ENV PATH=/opt/participant/bin:/opt/core/bin:/opt/swarm/bin:/usr/local/bin:/usr/bin:/bin PYTHONDONTWRITEBYTECODE=1
WORKDIR /epoch
ENTRYPOINT ["/opt/participant/bin/python", "-m", "ioalab_trial.agent"]
