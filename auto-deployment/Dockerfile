FROM registry.access.redhat.com/ubi8/python-39@sha256:496f9eee1837022e749a5f2d5c9ae91720200e03c38528894aee4eb6b3bb78b5

USER root

RUN INSTALL_PKGS="flask ansible_runner ansible-pylibssh jsonschema GitPython requests" && \
    pip install $INSTALL_PKGS

WORKDIR ~/issue_testing/issue_1160_MOC

COPY testapp.py ./

CMD [ "python", "./testapp.py"]
