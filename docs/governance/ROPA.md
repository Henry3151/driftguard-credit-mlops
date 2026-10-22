# Registro das Operações de Tratamento (LGPD art. 37)

| Campo | Descrição |
|---|---|
| **Operação** | Análise de risco de crédito e monitoramento contínuo do modelo de Credit Scoring |
| **Controlador** | Instituição concedente de crédito |
| **Operador** | Time de Engenharia de ML (plataforma DriftGuard) |
| **Encarregado (DPO)** | Canal: dpo@empresa.exemplo |
| **Titulares** | Proponentes de crédito pessoal |
| **Dados tratados** | Ver [inventário](PII_INVENTORY.md): financeiros, cadastrais e comportamento de pagamento |
| **Finalidade** | (1) Avaliar o risco de inadimplência na concessão; (2) garantir a qualidade e a estabilidade do modelo (monitoramento, drift, fairness) |
| **Bases legais** | Art. 7º, X — **proteção do crédito**; art. 7º, V — procedimentos preliminares a contrato a pedido do titular; art. 7º, IX — legítimo interesse para monitoramento e segurança do modelo (com teste de balanceamento) |
| **Compartilhamento** | Nenhum com terceiros. Bureaus de crédito são fontes (`ext_source_*`), não destinatários |
| **Transferência internacional** | Não há |
| **Retenção** | Ver [política de retenção](RETENTION.md) |
| **Medidas de segurança** | Pseudonimização HMAC, segregação de atributos sensíveis, minimização na fonte, dados fora do controle de versão, varredura automática de PII, trilha de auditoria (logs com `run_id`/`trace_id`, MLflow) |
| **Decisão automatizada** | Sim (art. 20). Garantias: motivos da decisão por proposta, revisão humana sob solicitação, model card publicado |
