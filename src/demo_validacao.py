"""Demo offline da camada de validação e retry.

Roda sem internet e sem GOOGLE_API_KEY. Demonstra os quatro requisitos do PDF:
  1. Parecer válido passando na validação.
  2. Parecer inválido falhando com mensagem clara.
  3. Retry com correção (mock): inválido -> corrigido -> válido.
  4. Onde a validação entra no pipeline integrado.

Além disso, exercita CrossReviewSchema e EditorVerdictSchema.

Uso:
    python src/demo_validacao.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from review_schema import (  # noqa: E402
    validar_cross_review,
    validar_editor_verdict,
    validar_review,
)
from validacao_retry import (  # noqa: E402
    MAX_TENTATIVAS,
    PipelineValidationError,
    ResultadoValidacao,
    corrigir_saida_mock,
    validar_com_tentativas,
)

EXAMPLES = HERE / "examples"

# ---------------------------------------------------------------------------
# Helpers de exibição
# ---------------------------------------------------------------------------

LARGURA = 70


def _sep(titulo: str = "") -> None:
    if titulo:
        print(f"\n{'=' * LARGURA}")
        print(f"  {titulo}")
        print("=" * LARGURA)
    else:
        print("-" * LARGURA)


def _passou(resultado: ResultadoValidacao) -> None:
    print(f"  STATUS   : PASSOU")
    print(f"  tentativas: {resultado.tentativas_usadas}/{MAX_TENTATIVAS}")
    if resultado.dados is not None:
        tipo = type(resultado.dados).__name__
        print(f"  tipo     : {tipo}")


def _falhou(resultado: ResultadoValidacao) -> None:
    print(f"  STATUS   : FALHOU")
    print(f"  tentativas: {resultado.tentativas_usadas}/{MAX_TENTATIVAS}")
    if resultado.erro_final:
        linhas = resultado.erro_final.splitlines()
        print("  erro     :")
        for linha in linhas[:12]:
            print(f"    {linha}")
        if len(linhas) > 12:
            print(f"    ... (+{len(linhas) - 12} linhas)")


def _historico(resultado: ResultadoValidacao) -> None:
    print("  histórico:")
    for entrada in resultado.historico:
        status = entrada["status"]
        t = entrada["tentativa"]
        erro = entrada.get("erro") or ""
        resumo = erro.split("\n")[0][:60] if erro else ""
        print(f"    tentativa {t}: {status}" + (f" — {resumo}" if resumo else ""))


def _carregar(nome: str) -> dict:
    caminho = EXAMPLES / nome
    return json.loads(caminho.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# Cenários
# ---------------------------------------------------------------------------

class _ModoMock:
    """Simula RunMode.MOCK sem importar pipeline.py (que precisa de ADK)."""
    value = "mock"


MODO_MOCK = _ModoMock()


def caso_1() -> None:
    _sep("CASO 1 — ReviewSchema — Parecer VÁLIDO (caminho feliz)")
    dados = _carregar("example_valid_output.json")
    resultado = validar_com_tentativas(dados, validar_review, MODO_MOCK, "statistician")
    _passou(resultado)
    print(f"  revisor  : {resultado.dados.revisor}")
    print(f"  nota_geral: {resultado.dados.nota_geral.nota}")


def caso_2() -> None:
    _sep("CASO 2 — ReviewSchema — Parecer INVÁLIDO (falha clara)")
    dados = _carregar("example_invalid_output.json")
    try:
        validar_com_tentativas(
            dados, validar_review, MODO_MOCK, "domain_expert", max_tentativas=1
        )
    except PipelineValidationError as exc:
        resultado = exc.resultado
        _falhou(resultado)
        _historico(resultado)
    print()
    print("  Violações presentes no arquivo:")
    print("    • originalidade.nota = 5  (máximo permitido é 4)")
    print("    • significancia.justificativa = '   '  (apenas espaços)")
    print("    • confianca.nota = 0  (mínimo é 1) + falta 'justificativa'")


def caso_3() -> None:
    _sep("CASO 3 — ReviewSchema — Retry mock: inválido -> corrigido -> VÁLIDO")
    dados = _carregar("example_invalid_output.json")
    resultado = validar_com_tentativas(dados, validar_review, MODO_MOCK, "domain_expert")
    _passou(resultado)
    _historico(resultado)
    print()
    print("  Campos reparados pelo corrigir_saida_mock:")
    corrigido = corrigir_saida_mock(dados, "")
    if corrigido.get("originalidade", {}).get("nota") != dados.get("originalidade", {}).get("nota"):
        print(f"    • originalidade.nota: {dados['originalidade']['nota']} -> {corrigido['originalidade']['nota']}")
    if corrigido.get("confianca", {}).get("nota") != dados.get("confianca", {}).get("nota"):
        nota_orig = dados.get("confianca", {}).get("nota", "?")
        nota_corr = corrigido.get("confianca", {}).get("nota", "?")
        print(f"    • confianca.nota: {nota_orig} -> {nota_corr}")
    sig_just_orig = dados.get("significancia", {}).get("justificativa", "")
    sig_just_corr = corrigido.get("significancia", {}).get("justificativa", "")
    if sig_just_orig != sig_just_corr:
        print(f"    • significancia.justificativa: {sig_just_orig!r} -> {sig_just_corr!r}")


def caso_4() -> None:
    _sep("CASO 4 — ReviewSchema — Esgotamento: PipelineValidationError")
    # O corrector mock repara valores mas NÃO cria campos ausentes.
    # Campos obrigatórios inteiramente faltantes são irrecuperáveis.
    dados_patologicos = {
        "revisor": "copyeditor",
        # solidez_tecnica, originalidade, significancia, clareza,
        # nota_geral e confianca estão completamente ausentes.
        # O corrector percorre os campos existentes; não pode criá-los do zero.
    }
    try:
        validar_com_tentativas(dados_patologicos, validar_review, MODO_MOCK, "copyeditor")
    except PipelineValidationError as exc:
        resultado = exc.resultado
        print(f"  PipelineValidationError capturada!")
        print(f"  tentativas esgotadas: {resultado.tentativas_usadas}/{MAX_TENTATIVAS}")
        _historico(resultado)
        print()
        print("  Campos ausentes (o corrector mock nao cria campos do zero):")
        print("    solidez_tecnica, originalidade, significancia, clareza,")
        print("    nota_geral, confianca — todos obrigatorios e ausentes.")
        print("  O pipeline bloqueia. O dado nao passa para a proxima fase.")


def caso_5() -> None:
    _sep("CASO 5 — CrossReviewSchema — Parecer VÁLIDO")
    dados_completos = _carregar("example_cross_review_output.json")
    entrada = dados_completos["statistician_cross_review"]
    resultado = validar_com_tentativas(entrada, validar_cross_review, MODO_MOCK, "statistician")
    _passou(resultado)
    print(f"  mudou_posicao: {resultado.dados.mudou_posicao}")
    print(f"  mudancas     : {len(resultado.dados.mudancas)} entrada(s)")


def caso_6() -> None:
    _sep("CASO 6 — CrossReviewSchema — Parecer INVÁLIDO (falha clara)")
    dados = _carregar("example_invalid_cross_review.json")
    try:
        validar_com_tentativas(
            dados, validar_cross_review, MODO_MOCK, "statistician", max_tentativas=1
        )
    except PipelineValidationError as exc:
        resultado = exc.resultado
        _falhou(resultado)
        _historico(resultado)
    print()
    print("  Violações presentes no arquivo:")
    print("    • mudou_posicao=true + mudancas=[]  (_coerencia_mudancas)")
    print("    • resposta_aos_pares='   '  (_valida_resposta)")


def caso_7() -> None:
    _sep("CASO 7 — EditorVerdictSchema — Veredito VÁLIDO")
    dados = _carregar("example_editor_verdict_output.json")
    resultado = validar_com_tentativas(dados, validar_editor_verdict, MODO_MOCK, "editor")
    _passou(resultado)
    print(f"  decisao      : {resultado.dados.decisao}")
    print(f"  criticas     : {len(resultado.dados.criticas)}")
    print(f"  recomendacoes: {len(resultado.dados.recomendacoes_aos_autores)}")


def caso_8() -> None:
    _sep("CASO 8 — EditorVerdictSchema — Veredito INVÁLIDO (falha clara)")
    dados = _carregar("example_invalid_editor_verdict.json")
    try:
        validar_com_tentativas(
            dados, validar_editor_verdict, MODO_MOCK, "editor", max_tentativas=1
        )
    except PipelineValidationError as exc:
        resultado = exc.resultado
        _falhou(resultado)
        _historico(resultado)
    print()
    print("  Violações presentes no arquivo:")
    print("    • decisao=5  (máximo é 4)")
    print("    • justificativa=''  (campo obrigatório vazio)")
    print("    • notas_por_revisor={}  (_notas_validas: não pode ser vazio)")
    print("    • recomendacoes_aos_autores=['...', '']  (_recomendacoes_nao_vazias)")


def caso_9() -> None:
    _sep("CASO 9 — Ponto de integração no pipeline")
    print()
    print("  A camada de validação/retry entra em src/pipeline.py nos")
    print("  seguintes pontos (3 fases × 2 modos = 6 chamadas):")
    print()
    print("  ┌─────────────────────────────────────────────────────────┐")
    print("  │  IndependentReviewPhase.run()                           │")
    print("  │    -> validar_com_tentativas(payload, validar_review,    │")
    print("  │        mode, rid)     ← para cada um dos 3 revisores   │")
    print("  ├─────────────────────────────────────────────────────────┤")
    print("  │  CrossReviewPhase.run()                                 │")
    print("  │    -> validar_com_tentativas(payload, validar_cross_     │")
    print("  │        review, mode, rid)                               │")
    print("  ├─────────────────────────────────────────────────────────┤")
    print("  │  EditorVerdictPhase.run()                               │")
    print("  │    -> validar_com_tentativas(payload, validar_editor_    │")
    print("  │        verdict, mode, 'editor')                         │")
    print("  └─────────────────────────────────────────────────────────┘")
    print()
    print("  Em modo MOCK  -> corrector: corrigir_saida_mock()  (offline)")
    print("  Em modo API   -> corrector: corrigir_saida_api()   (Gemini)")
    print()
    print("  Se todas as tentativas falharem:")
    print("    PipelineValidationError é levantada -> pipeline bloqueado")
    print("    -> erro nunca passa silenciosamente para a próxima fase.")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> None:
    print("=" * LARGURA)
    print("  DEMO: Camada de Validação e Retry — Pipeline Peer Review")
    print("  Grupo 1 · Integração Validação, Retry e Confiabilidade")
    print("  (offline — sem GOOGLE_API_KEY · sem internet)")
    print("=" * LARGURA)

    caso_1()
    caso_2()
    caso_3()
    caso_4()
    caso_5()
    caso_6()
    caso_7()
    caso_8()
    caso_9()

    _sep()
    print("  Demo concluída. Todos os 9 cenários executados.")
    print("  Para rodar o pipeline completo offline:")
    print("    python main.py mock")
    print("-" * LARGURA)


if __name__ == "__main__":
    main()
