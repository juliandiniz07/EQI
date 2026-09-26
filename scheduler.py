"""Agendador do coletor.

- Fundamentus: a cada 30 minutos, de segunda a sexta, das 7h às 22h.
- CVM Dados Abertos: uma vez por dia útil (o arquivo oficial é atualizado diariamente).
- Roda as duas coletas uma vez logo ao iniciar.

O fuso vem de TZ_AGENDA (padrão America/Sao_Paulo). Use TZ_AGENDA=UTC para o horário em UTC.
"""

import logging
import os

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger
from dotenv import load_dotenv

import eventos
import fetch_events

log = logging.getLogger("agendador")


def main():
    load_dotenv()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    fuso = os.getenv("TZ_AGENDA", "America/Sao_Paulo")

    log.info("Banco: %s | fuso da agenda: %s", eventos.db_path(), fuso)
    log.info("Coleta inicial...")
    fetch_events.executar_fundamentus()
    fetch_events.executar_cvm()

    agenda = BlockingScheduler(timezone=fuso)
    opcoes = {"max_instances": 1, "coalesce": True, "misfire_grace_time": 600}
    agenda.add_job(
        fetch_events.executar_fundamentus,
        CronTrigger(day_of_week="mon-fri", hour="7-21", minute="0,30", timezone=fuso),
        id="fundamentus",
        **opcoes,
    )
    agenda.add_job(
        fetch_events.executar_fundamentus,
        CronTrigger(day_of_week="mon-fri", hour=22, minute=0, timezone=fuso),
        id="fundamentus-22h",
        **opcoes,
    )
    agenda.add_job(
        fetch_events.executar_cvm,
        CronTrigger(day_of_week="mon-fri", hour=8, minute=15, timezone=fuso),
        id="cvm",
        **opcoes,
    )
    for job in agenda.get_jobs():
        log.info("Agendado: %s", job.id)
    log.info("Agendador rodando. Ctrl+C para sair.")
    try:
        agenda.start()
    except (KeyboardInterrupt, SystemExit):
        log.info("Agendador encerrado.")


if __name__ == "__main__":
    main()
