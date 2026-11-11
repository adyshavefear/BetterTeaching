import unittest
from unittest.mock import MagicMock, patch

from script_notas import (
    executar_em_aba_ativa,
    planejar_preenchimento,
    processar_cadernos_com_html,
    validar_mapeamento,
)


class NotasTest(unittest.TestCase):
    def setUp(self):
        self.alunos = [{"id_aluno": "aluno:1", "nome_oficial": "Aluno de teste"}]
        self.resultado = {
            "turma": "Turma de teste",
            "alunos": [{**self.alunos[0], "nota1": 0.0, "nota2": 10.0, "nota3": None}],
        }

    def test_aceita_limites_e_preserva_ausencia_de_nota(self):
        self.assertEqual(validar_mapeamento(self.resultado, self.alunos), self.resultado)

    def test_rejeita_identidade_duplicidade_e_notas_invalidas(self):
        aluno = self.resultado["alunos"][0]
        for alteracao in ({"id_aluno": "outro"}, {"nome_oficial": "Outro aluno"},
                          {"nota1": -1.0}, {"nota1": 11.0}, {"nota1": float("nan")},
                          {"nota1": "9"}):
            with self.subTest(alteracao=alteracao), self.assertRaises(ValueError):
                validar_mapeamento({"turma": "teste", "alunos": [{**aluno, **alteracao}]}, self.alunos)
        with self.assertRaises(ValueError):
            validar_mapeamento({"turma": "teste", "alunos": [aluno, aluno]}, self.alunos)

    def test_campo_ausente_ou_ambiguo_cancela_antes_de_preencher(self):
        for quantidade in (0, 2):
            page = MagicMock()
            campo = page.locator.return_value.locator.return_value
            campo.count.return_value = quantidade
            with self.assertRaises(ValueError):
                planejar_preenchimento(page, self.resultado, self.alunos, 3)
            campo.fill.assert_not_called()

    def test_plano_identifica_campos_sem_usar_posicao(self):
        page = MagicMock()
        page.locator.return_value.locator.return_value.count.return_value = 1
        plano = planejar_preenchimento(page, self.resultado, self.alunos, 3)
        self.assertEqual([item[1] for item in plano], ["0,0", "10,0"])
        page.locator.assert_called_with('[id="aluno:1"]')

    def test_erro_permanente_da_api_nao_e_repetido(self):
        with patch("script_notas.genai.Client") as client:
            chamada = client.return_value.models.generate_content
            chamada.side_effect = RuntimeError("401: invalid credentials")
            with self.assertRaises(RuntimeError):
                processar_cadernos_com_html([], self.alunos)
            self.assertEqual(chamada.call_count, 1)

    def test_bimestre_invalido_nao_abre_navegador(self):
        with patch("script_notas.sync_playwright") as navegador:
            with self.assertRaises(ValueError):
                executar_em_aba_ativa([], 5)
            navegador.assert_not_called()


if __name__ == "__main__":
    unittest.main()
