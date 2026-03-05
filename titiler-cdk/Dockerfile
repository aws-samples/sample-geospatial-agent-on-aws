FROM ghcr.io/developmentseed/titiler:0.24.2

# Install AWS Lambda Web Adapter
COPY --from=public.ecr.aws/awsguru/aws-lambda-adapter:0.8.3 /lambda-adapter /opt/extensions/lambda-adapter

# Lambda Web Adapter expects port 8080
ENV PORT=8080

# Override the default CMD to use port 8080
CMD ["uvicorn", "titiler.application.main:app", "--host", "0.0.0.0", "--port", "8080", "--workers", "1"]
