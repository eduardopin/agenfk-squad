# ADR-0007: Confirmação de pagamento assíncrona com fila e workers no EKS

- Status: Proposto
- Data: 2026-10-20
- Decisores: Arquitetura de Plataforma, Squad Checkout

## Contexto

O checkout chama o provedor de pagamento de forma síncrona. Em picos (Black Friday) a latência p99 do provedor passa de 8 s e o checkout expira, gerando pedidos duplicados quando o cliente clica de novo. Precisamos desacoplar a confirmação do pagamento da requisição do usuário.

## Decisão

Publicar um evento `payment.requested` em uma fila SQS e processar a cobrança em workers no EKS. O checkout responde imediatamente "pagamento em processamento" e a tela de pedido consulta o estado. O worker chama o provedor e grava o resultado no banco de pedidos.

Os workers escalam por profundidade da fila via KEDA. A fila terá DLQ após 5 tentativas.

## Alternativas consideradas

- Aumentar o timeout do checkout para 15 s
- Usar Step Functions para orquestrar a cobrança
- Manter síncrono e adicionar circuit breaker

## Consequências

- O usuário pode ver "em processamento" por alguns segundos
- Reduz timeouts do checkout em picos
- Exige monitorar a profundidade da fila
