#Using XwangNet Base Image
FROM xwangnet-base-v1

LABEL maintainer="zeuschan0111@gmail.com"

# Choose qemu architecture
RUN apt-get update -qy \
  && apt-get upgrade -qy \
  && apt-get install -y \
  #  qemu-system-mipsel \
  #  qemu-system-arm \
    qemu-system-mips \
  && apt-get clean && rm -rf /var/lib/apt/lists/*


# Node-Specific Files
COPY ./node-resource/docker/ /xwangnet/

#################################################################
# HEALTHCHECK & ENTRYPOINT already defined in the base image, only override if needed

# # Set up health check with a 2-minute startup delay
#HEALTHCHECK --interval=30s --timeout=10s --start-period=60s CMD ["/xwangnet/healthcheck.py"]

# # Set the entry point
# ENTRYPOINT ["/xwangnet/start-qemu.py"]
