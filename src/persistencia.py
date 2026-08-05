"""Persistência de checkpoints por fase — Grupo 1.

Responsabilidade única: salvar e carregar o resultado de cada fase do pipeline
em disco, correlacionado por run_id. Não conhece peer review, agentes ou schemas
— é um mecanismo genérico de checkpoint que qualquer pipeline pode usar.

Estrutura de pastas gerada:
    src/logs/checkpoints/<run_id>/fase_0_extracao_pdf.json
    src/logs/checkpoints/<run_id>/fase_1_revisao_independente.json
    src/logs/checkpoints/<run_id>/fase_2_leitura_cruzada.json
    src/logs/checkpoints/<run_id>/fase_3_editor_chefe.json
    src/logs/checkpoints/<run_id>.meta.json      <- sidecar (NÃO é uma fase)
    src/logs/checkpoints/<run_id>.estado.json    <- sidecar (NÃO é uma fase)

Duas categorias de arquivo, deliberadamente separadas:

- **Checkpoint de fase** (``save``/``load``) — a saída de uma fase, DENTRO da
  pasta ``<run_id>/``. É o que ``fases_concluidas()`` enxerga e o que o
  ``Pipeline`` usa para pular fases numa retomada.
- **Estado auxiliar** (``salvar_estado``/``carregar_estado``) — tudo que
  precisa sobreviver à retomada mas NÃO é saída de fase: métricas já
  coletadas, metadados da execução, marcação de execução concluída. Fica em
  arquivos *sidecar* IRMÃOS da pasta ``<run_id>/``, e não dentro dela, porque
  ``fases_concluidas()`` lista os ``*.json`` da pasta — um estado guardado lá
  dentro viraria uma "fase fantasma" na retomada e no resumo de falha.

Toda escrita é atômica (grava em .tmp e renomeia): se o processo morrer no meio
da escrita, o arquivo anterior permanece intacto em vez de ficar corrompido
pela escrita parcial.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


class CheckpointManager:
    """Gerencia checkpoints de fases de uma execução do pipeline.

    Cada instância representa uma execução específica (run_id). Fases concluídas
    são gravadas como JSONs individuais dentro de um subdiretório do run_id.
    """

    def __init__(self, checkpoint_dir: str | Path, run_id: str) -> None:
        self.run_id = run_id
        self.raiz = Path(checkpoint_dir)
        self.dir = self.raiz / run_id
        self.dir.mkdir(parents=True, exist_ok=True)

    # -- escrita ---------------------------------------------------------------

    @staticmethod
    def _escrever_atomico(destino: Path, dados: dict[str, Any]) -> Path:
        """Grava ``dados`` como JSON em ``destino`` sem deixar arquivo parcial.

        Escreve em um arquivo temporário (.tmp) e só renomeia para o nome final
        após a escrita completa — uma interrupção no meio da gravação preserva
        o conteúdo anterior em vez de corromper o arquivo.
        """
        tmp = destino.with_suffix(destino.suffix + ".tmp")
        tmp.write_text(json.dumps(dados, indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(destino)
        return destino

    # -- checkpoints de fase ---------------------------------------------------

    def _path(self, fase: str) -> Path:
        return self.dir / f"{fase}.json"

    def save(self, fase: str, dados: dict[str, Any]) -> Path:
        """Grava o resultado de uma fase em disco de forma atômica.

        Returns
        -------
        Path
            Caminho do arquivo de checkpoint gravado.
        """
        return self._escrever_atomico(self._path(fase), dados)

    def load(self, fase: str) -> dict[str, Any] | None:
        """Carrega o checkpoint de uma fase, ou None se ainda não existe."""
        path = self._path(fase)
        if not path.exists():
            return None
        return json.loads(path.read_text(encoding="utf-8"))

    def fases_concluidas(self) -> list[str]:
        """Devolve os nomes das fases que já têm checkpoint salvo, em ordem alfabética."""
        return sorted(p.stem for p in self.dir.glob("*.json"))

    def caminho(self, fase: str) -> Path:
        """Devolve o Path do arquivo de checkpoint de uma fase (exista ou não)."""
        return self._path(fase)

    def remover_fase(self, fase: str) -> bool:
        """Apaga o checkpoint de uma fase. Devolve True se havia algo para apagar."""
        path = self._path(fase)
        if not path.exists():
            return False
        path.unlink()
        return True

    def limpar_fases(self) -> list[str]:
        """Apaga TODOS os checkpoints de fase desta execução.

        Usado pela re-execução forçada (``--force``): a próxima chamada roda
        tudo de novo, mantendo o mesmo ``run_id`` (e portanto o mesmo trace e a
        mesma trilha de eventos). Os sidecars de estado NÃO são tocados aqui —
        quem força a re-execução decide o que fazer com eles.
        """
        removidas = self.fases_concluidas()
        for fase in removidas:
            self._path(fase).unlink()
        return removidas

    # -- estado auxiliar (sidecars, fora da pasta do run) ----------------------

    def caminho_estado(self, nome: str) -> Path:
        """Path do sidecar ``<run_id>.<nome>.json`` (exista ou não)."""
        return self.raiz / f"{self.run_id}.{nome}.json"

    def salvar_estado(self, nome: str, dados: dict[str, Any]) -> Path:
        """Grava (atomicamente) um estado auxiliar da execução.

        Estado auxiliar é o que precisa sobreviver a uma retomada sem ser saída
        de fase: métricas já coletadas, metadados da execução (pdf_path, modo),
        marcação de execução concluída. Fica em um sidecar irmão da pasta do
        run — ver a docstring do módulo para o porquê.
        """
        return self._escrever_atomico(self.caminho_estado(nome), dados)

    def carregar_estado(self, nome: str) -> dict[str, Any] | None:
        """Carrega um estado auxiliar, ou None se ele ainda não existe."""
        path = self.caminho_estado(nome)
        if not path.exists():
            return None
        return json.loads(path.read_text(encoding="utf-8"))
