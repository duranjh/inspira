"""Localization table for SSE ``thinking_message`` strings emitted from the
kickoff + topic-turn streaming endpoints.

These messages render in the front-end's loading state while the LLM call
is in flight. Earlier they were hardcoded English literals at the emit
site (``api.py``'s SSE generators); P1.7 (#067) localizes them so a user
on the Spanish UI doesn't see "Reading your idea…" while everything else
on the page is in Spanish.

Design choice — backend-side localization vs key-passthrough:
  We keep the wire format ``{message: str}`` unchanged and resolve the
  language inside the backend, rather than sending an i18n key for the
  FE to resolve. Pros: zero FE change, the SSE consumer keeps treating
  ``message`` as a display string. Cons: 9-locale translations live in
  Python rather than alongside the FE i18n JSON. Acceptable trade for
  ~13 short strings that change rarely; if the set grows, revisit.

Fallback rule:
  Lookups for an unknown locale OR an unknown key fall back to English.
  Unknown keys are also a coding error — production should log a
  warning, but we don't raise to avoid hard-breaking the SSE stream.
"""
from __future__ import annotations

import logging
from typing import Final

logger = logging.getLogger(__name__)


# Each key maps locale → translated string. English is always present.
# When extending, KEEP the en entry — it's the fallback for any locale
# that hasn't been translated yet.
THINKING_MESSAGES: Final[dict[str, dict[str, str]]] = {
    "kickoff.reading_idea": {
        "en": "Reading your idea…",
        "es": "Leyendo tu idea…",
        "fr": "Lecture de votre idée…",
        "de": "Lese deine Idee…",
        "pt": "Lendo sua ideia…",
        "ja": "アイデアを読んでいます…",
        "it": "Leggo la tua idea…",
        "nl": "Je idee aan het lezen…",
        "pl": "Czytam twój pomysł…",
    },
    "kickoff.finding_shape": {
        "en": "Finding the shape of your idea…",
        "es": "Encontrando la forma de tu idea…",
        "fr": "Trouve la forme de votre idée…",
        "de": "Erkenne die Struktur deiner Idee…",
        "pt": "Encontrando o formato da sua ideia…",
        "ja": "アイデアの輪郭を探っています…",
        "it": "Cerco la forma della tua idea…",
        "nl": "Op zoek naar de vorm van je idee…",
        "pl": "Szukam kształtu twojego pomysłu…",
    },
    "kickoff.sketching_topics": {
        "en": "Sketching topics…",
        "es": "Esbozando los temas…",
        "fr": "Esquisse des sujets…",
        "de": "Skizziere die Themen…",
        "pt": "Esboçando os tópicos…",
        "ja": "トピックをスケッチしています…",
        "it": "Abbozzo i temi…",
        "nl": "Onderwerpen schetsen…",
        "pl": "Szkicuję tematy…",
    },
    "kickoff.mapping_connections": {
        "en": "Mapping how they connect…",
        "es": "Mapeando cómo se conectan…",
        "fr": "Trace les liens entre eux…",
        "de": "Bilde die Verbindungen ab…",
        "pt": "Mapeando como se conectam…",
        "ja": "つながりをマッピングしています…",
        "it": "Mappa come si collegano…",
        "nl": "Verbindingen in kaart brengen…",
        "pl": "Mapuję powiązania…",
    },
    "kickoff.polishing_map": {
        "en": "Polishing the map…",
        "es": "Puliendo el mapa…",
        "fr": "Peaufine la carte…",
        "de": "Verfeinere die Karte…",
        "pt": "Refinando o mapa…",
        "ja": "マップを仕上げています…",
        "it": "Rifinisco la mappa…",
        "nl": "De kaart bijwerken…",
        "pl": "Dopracowuję mapę…",
    },
    "turn.reading_thread": {
        "en": "Reading the thread…",
        "es": "Leyendo el hilo…",
        "fr": "Lecture du fil…",
        "de": "Lese den Verlauf…",
        "pt": "Lendo a conversa…",
        "ja": "スレッドを読んでいます…",
        "it": "Leggo la conversazione…",
        "nl": "Het gesprek aan het lezen…",
        "pl": "Czytam wątek…",
    },
    "turn.weighing_options": {
        "en": "Weighing options…",
        "es": "Sopesando las opciones…",
        "fr": "Pèse les options…",
        "de": "Wäge Optionen ab…",
        "pt": "Avaliando opções…",
        "ja": "選択肢を検討しています…",
        "it": "Valuto le opzioni…",
        "nl": "Opties afwegen…",
        "pl": "Ważę opcje…",
    },
    "turn.framing_question": {
        "en": "Framing the question…",
        "es": "Formulando la pregunta…",
        "fr": "Formule la question…",
        "de": "Formuliere die Frage…",
        "pt": "Formulando a pergunta…",
        "ja": "質問を組み立てています…",
        "it": "Formulo la domanda…",
        "nl": "De vraag formuleren…",
        "pl": "Formułuję pytanie…",
    },
    "turn.drafting_response": {
        "en": "Drafting a response…",
        "es": "Redactando una respuesta…",
        "fr": "Rédige une réponse…",
        "de": "Entwerfe eine Antwort…",
        "pt": "Redigindo uma resposta…",
        "ja": "返信を作成しています…",
        "it": "Scrivo una risposta…",
        "nl": "Een antwoord opstellen…",
        "pl": "Piszę odpowiedź…",
    },
    "turn.polishing_phrasing": {
        "en": "Polishing the phrasing…",
        "es": "Puliendo la redacción…",
        "fr": "Peaufine la formulation…",
        "de": "Verfeinere die Formulierung…",
        "pt": "Refinando a redação…",
        "ja": "表現を仕上げています…",
        "it": "Rifinisco la formulazione…",
        "nl": "Formulering bijwerken…",
        "pl": "Dopracowuję sformułowanie…",
    },
    # Shared between kickoff + turn — both ramps end with the same
    # "still working" trio after the topic-specific lines run out.
    "common.taking_a_moment": {
        "en": "Hold on — this one's taking a moment…",
        "es": "Un momento, esto está llevando un poco más…",
        "fr": "Un instant — cela prend un peu plus de temps…",
        "de": "Einen Moment — das dauert etwas länger…",
        "pt": "Aguarde, está demorando um pouco…",
        "ja": "もう少しお待ちください…",
        "it": "Un attimo — ci sta mettendo un po'…",
        "nl": "Even geduld — dit duurt wat langer…",
        "pl": "Chwilę, to zajmuje trochę dłużej…",
    },
    "common.still_working": {
        "en": "Still working on it…",
        "es": "Todavía trabajando en ello…",
        "fr": "On continue…",
        "de": "Arbeite weiter daran…",
        "pt": "Ainda trabalhando nisso…",
        "ja": "まだ作業中です…",
        "it": "Sto ancora lavorando…",
        "nl": "Bezig…",
        "pl": "Wciąż pracuję…",
    },
    "common.still_working_long": {
        "en": "Still working — this can take a bit…",
        "es": "Todavía trabajando — esto puede tardar un poco…",
        "fr": "Toujours en cours — cela peut prendre un moment…",
        "de": "Arbeite weiter — das kann etwas dauern…",
        "pt": "Ainda trabalhando — pode levar um pouco…",
        "ja": "まだ作業中です — 少しかかる場合があります…",
        "it": "Ancora al lavoro — può richiedere un po'…",
        "nl": "Bezig — dit kan even duren…",
        "pl": "Wciąż pracuję — może to chwilę zająć…",
    },
    # ---- Artifact viewer (W2 ι) ------------------------------------------
    # Code-gen ramp shown in the Claude-style chat sidebar while the
    # scaffold is being drafted or refined. Full localization to match
    # the kickoff + turn ramps' coverage discipline.
    "artifact.scaffolding": {
        "en": "Sketching the file layout…",
        "es": "Esbozando la estructura de archivos…",
        "fr": "Esquisse de l'arborescence…",
        "de": "Skizziere das Dateilayout…",
        "pt": "Esboçando a estrutura de arquivos…",
        "ja": "ファイル構成を描いています…",
        "it": "Abbozzo la struttura dei file…",
        "nl": "Bestandstructuur schetsen…",
        "pl": "Szkicuję układ plików…",
    },
    "artifact.writing_files": {
        "en": "Writing the components…",
        "es": "Escribiendo los componentes…",
        "fr": "Écriture des composants…",
        "de": "Schreibe die Komponenten…",
        "pt": "Escrevendo os componentes…",
        "ja": "コンポーネントを書いています…",
        "it": "Scrivo i componenti…",
        "nl": "Componenten schrijven…",
        "pl": "Piszę komponenty…",
    },
    "artifact.connecting_pieces": {
        "en": "Wiring it together…",
        "es": "Conectando las piezas…",
        "fr": "Raccordement des éléments…",
        "de": "Verknüpfe die Teile…",
        "pt": "Conectando as peças…",
        "ja": "全体をつなげています…",
        "it": "Collego i pezzi…",
        "nl": "Onderdelen aansluiten…",
        "pl": "Łączę elementy…",
    },
    "artifact.polishing": {
        "en": "Polishing the edges…",
        "es": "Puliendo los detalles…",
        "fr": "Peaufinage des finitions…",
        "de": "Verfeinere die Details…",
        "pt": "Refinando os detalhes…",
        "ja": "細部を仕上げています…",
        "it": "Rifinisco i dettagli…",
        "nl": "Details bijwerken…",
        "pl": "Dopracowuję szczegóły…",
    },
}


def thinking_message(key: str, locale: str | None) -> str:
    """Return the localized thinking-message for ``key``, falling back to
    English when the locale is unsupported or the locale-specific
    translation is missing. Falls back to the literal ``key`` and logs a
    warning when ``key`` itself is unknown — that's a coding error and
    surfaces in the FE rather than blowing up the SSE stream.
    """
    table = THINKING_MESSAGES.get(key)
    if table is None:
        logger.warning(
            "thinking_message: unknown key %r — falling back to literal", key,
        )
        return key
    if not locale:
        return table["en"]
    primary = locale.lower().split("-")[0]
    return table.get(primary) or table["en"]
