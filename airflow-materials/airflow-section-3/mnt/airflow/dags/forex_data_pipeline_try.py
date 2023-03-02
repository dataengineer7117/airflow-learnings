from airflow import DAG
from airflow.providers.http.sensors.http import HttpSensor
from airflow.sensors.filesystem import FileSensor
from airflow.operators.python import PythonOperator
from airflow.operators.bash import BashOperator
from airflow.providers.apache.hive.operators.hive import HiveOperator
from airflow.providers.apache.spark.operators.spark_submit import SparkSubmitOperator
from airflow.operators.email import EmailOperator
from airflow.providers.slack.operators.slack_webhook import SlackWebhookOperator

import requests
import csv
import json
from datetime import datetime, timedelta


# Download forex rates according to the currencies we want to watch
# described in the file forex_currencies.csv
def download_rates():
    BASE_URL = "https://gist.githubusercontent.com/marclamberti/f45f872dea4dfd3eaa015a4a1af4b39b/raw/"
    ENDPOINTS = {
        'USD': 'api_forex_exchange_usd.json',
        'EUR': 'api_forex_exchange_eur.json'
    }
    with open('/opt/airflow/dags/files/forex_currencies.csv') as forex_currencies:
        reader = csv.DictReader(forex_currencies, delimiter=';')
        for idx, row in enumerate(reader):
            base = row['base']
            with_pairs = row['with_pairs'].split(' ')
            indata = requests.get(f"{BASE_URL}{ENDPOINTS[base]}").json()
            outdata = {'base': base, 'rates': {}, 'last_update': indata['date']}
            for pair in with_pairs:
                outdata['rates'][pair] = indata['rates'][pair]
            with open('/opt/airflow/dags/files/forex_rates.json', 'a') as outfile:
                json.dump(outdata, outfile)
                outfile.write('\n')


default_args = {
    "owner": "airflow",
    "email_on_failure": False,
    "email_on_retry": False,
    "email": "linkedafaque@gmail.com",
    "retries": 1,
    "retry_delay": timedelta(minutes=5)
}


def _get_message() -> str:
    return "Hi, from forex_data_pipeline"


with DAG(
    "forex_data_pipeline_try",
    start_date=datetime(2023, 1, 1),
    schedule_interval="@daily",
    default_args=default_args,
    catchup=False
) as dag:

    is_forex_rates_available = HttpSensor(
        task_id="is_forex_rates_available",
        http_conn_id="forex_api", # id of the connection containing api to be verified
        endpoint="marclamberti/f45f872dea4dfd3eaa015a4a1af4b39b/raw/818c752015ae03b8a4d3ec14d0d4ee4227c717e5", # anything after the host
        response_check=lambda response: "rates" in response.text,
        poke_interval=5, # check every 5 seconds
        timeout=20
    )

    is_forex_currencies_file_available = FileSensor(
        task_id="is_forex_currencies_file_available",
        fs_conn_id="forex_path",
        filepath="forex_currencies.csv",
        poke_interval=5,
        timeout=20
    )

    downloading_rates = PythonOperator(
        task_id="downloading_rates",
        python_callable=download_rates
    )

    saving_rates = BashOperator(
        task_id="saving_rates",
        bash_command="""
            hdfs dfs -mkdir -p /forex && \
            hdfs dfs -put -f $AIRFLOW_HOME/dags/files/forex_rates.json /forex
        """
    )

    creating_forex_rates_table = HiveOperator(
        task_id="creating_forex_rates_table", 
        hive_cli_conn_id="hive_conn",
        hql="""
            CREATE EXTERNAL TABLE IF NOT EXISTS forex_rates(
                base STRING,
                last_update DATE,
                eur DOUBLE,
                usd DOUBLE,
                nzd DOUBLE,
                gbp DOUBLE,
                jpy DOUBLE,
                cad DOUBLE
                )
            ROW FORMAT DELIMITED
            FIELDS TERMINATED BY ','
            STORED AS TEXTFILE
        """
    )

    forex_processing = SparkSubmitOperator(
        task_id="forex_processing",
        application="/opt/airflow/dags/scripts/forex_processing.py",
        conn_id="spark_conn"
    )

    send_email_notification = EmailOperator(
        task_id="send_email_notification",
        to="linkedafaque@gmail.com",
        subject="forex_data_pipeline_try",
        html_content="<h3>forex_data_pipeline</h3>"
    )

    send_slack_notification = SlackWebhookOperator(
        task_id="send_slack_notification",
        http_conn_id="slack_conn",
        message=_get_message(),
        channel="#airflow-poc-alerts"
    )

    is_forex_rates_available >> is_forex_currencies_file_available >> downloading_rates 
    downloading_rates >> saving_rates >> creating_forex_rates_table >> forex_processing 
    forex_processing >> send_email_notification >> send_slack_notification
