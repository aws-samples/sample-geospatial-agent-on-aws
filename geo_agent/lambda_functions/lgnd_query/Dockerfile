FROM public.ecr.aws/lambda/python:3.12

# Install duckdb with httpfs extension pre-loaded
RUN pip install duckdb==1.3.0 pytz numpy --no-cache-dir

# Pre-install httpfs extension into a persistent location
RUN python -c "import duckdb; con = duckdb.connect(); con.execute(\"SET home_directory='/var/task'\"); con.execute(\"SET extension_directory='/var/task/duckdb_extensions'\"); con.execute('INSTALL httpfs'); con.close(); print('httpfs installed')"

COPY handler.py ${LAMBDA_TASK_ROOT}/

CMD ["handler.handler"]
