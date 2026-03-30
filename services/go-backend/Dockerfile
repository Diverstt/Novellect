FROM golang:1.25-alpine AS builder
WORKDIR /src
COPY . /src
RUN go build -o /out/novellect-go ./cmd/server
FROM alpine:3.20
WORKDIR /app
COPY --from=builder /out/novellect-go /app/novellect-go
EXPOSE 8080
CMD ["/app/novellect-go"]
