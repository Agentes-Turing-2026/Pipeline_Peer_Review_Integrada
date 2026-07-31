"""Persistência de checkpoints por fase — Grupo 1.

Responsabilidade única: salvar e carregar o resultado de cada fase do pipeline
em disco, correlacionado por run_id. Não conhece peer review, agentes ou schemas
— é um mecanismo genérico de checkpoint que qualquer pipeline pode usar.

Estrutura de pastas gerada:
    src/logs/checkpoints/<run_id>/fase_1_revisao_independente.json
    src/logs/checkpoints/<run_id>/fase_2_leitura_cruzada.json
    src/logs/checkpoints/<run_id>/fase_3_editor_chefe.json

O save() usa escrita atômica (grava em .tmp e renomeia): se o processo morrer
no meio da escrita, o arquivo anterior permanece intacto em vez de ficar
corrompido pela escrita parcial.
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
        self.dir = Path(checkpoint_dir) / run_id
        self.dir.mkdir(parents=True, exist_ok=True)

    def _path(self, fase: str) -> Path:
        return self.dir / f"{fase}.json"

    def save(self, fase: str, dados: dict[str, Any]) -> Path:
        """Grava o resultado de uma fase em disco de forma atômica.

        Escreve em um arquivo temporário (.tmp) e só renomeia para o nome
        final após a escrita completa — garante que uma interrupção no meio
        da gravação não deixe o checkpoint corrompido.

        Returns
        -------
        Path
            Caminho do arquivo de checkpoint gravado.
        """
        destino = self._path(fase)
        tmp = destino.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(dados, indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(destino)
        return destino

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
