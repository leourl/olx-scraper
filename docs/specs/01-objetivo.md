# 01 — Objetivo

## Contexto

Com o objetivo de comprar um **Dell OptiPlex** ou **Lenovo ThinkCentre** usado,
navegar pelos anúncios da OLX é frustrante:

- os preços são inconsistentes e difíceis de comparar;
- as informações sobre o hardware (CPU, RAM, armazenamento, formato) não seguem
  um padrão — cada vendedor escreve diferente;
- muito anúncio é incompleto ou confuso.

## O que queremos construir

Um sistema de **scraping da OLX** que:

1. **Coleta** anúncios (título, descrição, preço, link, cidade, imagem);
2. **Persiste** em um banco de dados;
3. **Extrai specs estruturados** do hardware usando uma **LLM** (com fallback
   determinístico por regex), em vez de depender só de regex sobre texto
   bagunçado;
4. **Expõe** os resultados por **API REST** e **interface web** para consultar,
   filtrar e comparar.

## Objetivos não-funcionais

- Baixo custo de LLM (não chamar à toa, cachear por URL);
- Robustez a mudanças no HTML da OLX;
- Nunca quebrar o anúncio se a extração falhar (raw sempre salvo);
- Ser executável localmente, sem infraestrutura complexa.

## Não-objetivos (fora de escopo)

- Comprar/integrar com a OLX (sem login, sem API oficial);
- Suporte a múltiplos sites de anúncios (por ora só OLX);
- Alertas em tempo real / notificações push;
- Deploy multi-usuário com autenticação.

## Critérios de sucesso

- [ ] Consigo filtrar "OptiPlex ou ThinkCentre, RAM ≥ 8GB, ≤ R$ 800" e confiar no resultado
- [ ] Preço é sempre extraído corretamente (campo determinístico)
- [ ] Um anúncio novo nunca duplica (dedup por URL)
- [ ] Reprocessar specs não custa caro (cache)
