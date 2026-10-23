FROM eclipse-temurin:21-jdk-jammy AS build
WORKDIR /workspace
COPY . .
RUN sh ./gradlew :app:installDist --no-daemon

FROM eclipse-temurin:21-jre-jammy
WORKDIR /app
COPY --from=build /workspace/app/build/install/app/ ./
EXPOSE 8080
CMD ["./bin/app"]
