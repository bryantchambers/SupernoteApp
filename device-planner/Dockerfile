# Orchestrator-approved isolated build. Google's Linux Android tools require x86_64.
FROM --platform=linux/amd64 node:22.14.0-bookworm
RUN apt-get update && apt-get install -y --no-install-recommends openjdk-17-jdk-headless unzip ca-certificates curl python3 \
    && rm -rf /var/lib/apt/lists/*
ENV ANDROID_HOME=/opt/android-sdk
ENV PATH="/opt/android-sdk/cmdline-tools/latest/bin:/opt/android-sdk/platform-tools:${PATH}"
RUN curl -fsSL https://dl.google.com/android/repository/commandlinetools-linux-11076708_latest.zip -o /tmp/android-tools.zip \
    && mkdir -p /opt/android-sdk/cmdline-tools \
    && unzip -q /tmp/android-tools.zip -d /opt/android-sdk/cmdline-tools \
    && mv /opt/android-sdk/cmdline-tools/cmdline-tools /opt/android-sdk/cmdline-tools/latest \
    && rm /tmp/android-tools.zip \
    && yes | sdkmanager --licenses >/dev/null \
    && sdkmanager 'platform-tools' 'platforms;android-35' 'build-tools;35.0.0' 'ndk;27.1.12297006'
WORKDIR /build
RUN npx --yes @react-native-community/cli@18.0.0 init SupernotePlanner \
    --template @supernote-plugin/sn-plugin-template@1.0.13 --version 0.79.2 --skip-install
WORKDIR /build/SupernotePlanner
RUN npm install --save-exact sn-plugin-lib@0.2.3
RUN apt-get update && apt-get install -y --no-install-recommends zip && rm -rf /var/lib/apt/lists/*
COPY . /source/device-planner
RUN mkdir -p /tmp/planner-storage-tests \
    && javac -encoding UTF-8 --release 8 -d /tmp/planner-storage-tests /source/device-planner/native/PlannerStateCodec.java /source/device-planner/native-tests/PlannerStateCodecTest.java \
    && java -cp /tmp/planner-storage-tests com.supernoteplanner.PlannerStateCodecTest
RUN SN_PLUGIN_SDK_ROOT=/build/SupernotePlanner/node_modules/sn-plugin-lib \
    node --experimental-vm-modules --test /source/device-planner/core.test.mjs \
    /source/device-planner/engine.test.mjs /source/device-planner/sdk-contract.test.mjs
RUN python3 /source/device-planner/prepare.py /build/SupernotePlanner
RUN --mount=type=cache,target=/root/.gradle bash ./buildPlugin.sh \
    && test -n "$(find build/outputs -name '*.snplg' -print -quit)"
CMD ["sh", "-c", "cp -R build/outputs/. /outputs/"]
