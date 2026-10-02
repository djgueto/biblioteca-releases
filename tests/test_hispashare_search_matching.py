"""
Tests para la lógica de matching cross-idioma en HispashareSearcher.

Cubre:
- _translate_words: sustitución de artículos/preposiciones español→inglés
- _remove_stop_words: eliminación de palabras vacías (ES + EN)
- _word_sim: similitud fuzzy entre dos palabras individuales
- token_match_score: score de coincidencia por tokens, cross-language
- build_query_candidates: generación de variantes de búsqueda
- calculate_result_score: score total para un resultado de búsqueda
- select_best_match: selección del mejor resultado con threshold
"""

import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch
from difflib import SequenceMatcher

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))


def _make_searcher():
    """Instancia de HispashareSearcher sin driver real."""
    from core.hispashare.searcher import HispashareSearcher
    mock_driver = MagicMock()
    return HispashareSearcher(driver=mock_driver)


def _result(title, year=None, last_pub=""):
    return {'url': 'https://hispashare.org/x', 'title': title, 'year': year, 'genre': '', 'last_publication': last_pub}


# ---------------------------------------------------------------------------
# _translate_words
# ---------------------------------------------------------------------------

class TestTranslateWords(unittest.TestCase):

    def setUp(self):
        self.s = _make_searcher()

    def test_los_to_the(self):
        self.assertEqual(self.s._translate_words("los simpson"), "the simpson")

    def test_las_to_the(self):
        result = self.s._translate_words("las chicas del cable")
        # 'las'→'the', 'del'→'of'
        self.assertEqual(result, "the chicas of cable")

    def test_el_to_the(self):
        self.assertEqual(self.s._translate_words("el reino"), "the reino")

    def test_la_to_the(self):
        self.assertEqual(self.s._translate_words("la casa de papel"), "the casa of papel")

    def test_y_to_and(self):
        self.assertEqual(self.s._translate_words("rick y morty"), "rick and morty")

    def test_de_to_of(self):
        self.assertEqual(self.s._translate_words("house de papel"), "house of papel")

    def test_no_change_for_english(self):
        result = self.s._translate_words("fortnite x the simpsons")
        self.assertEqual(result, "fortnite x the simpsons")

    def test_unknown_words_preserved(self):
        result = self.s._translate_words("stranger things")
        self.assertEqual(result, "stranger things")

    def test_fortnite_x_los_simpson(self):
        result = self.s._translate_words("fortnite x los simpson")
        self.assertEqual(result, "fortnite x the simpson")


# ---------------------------------------------------------------------------
# _remove_stop_words
# ---------------------------------------------------------------------------

class TestRemoveStopWords(unittest.TestCase):

    def setUp(self):
        self.s = _make_searcher()

    def test_removes_spanish_articles(self):
        result = self.s._remove_stop_words("fortnite x los simpson")
        self.assertNotIn("los", result.split())
        self.assertIn("fortnite", result.split())
        self.assertIn("simpson", result.split())

    def test_removes_english_articles(self):
        result = self.s._remove_stop_words("fortnite x the simpsons")
        self.assertNotIn("the", result.split())
        self.assertIn("simpsons", result.split())

    def test_keeps_content_words(self):
        result = self.s._remove_stop_words("breaking bad")
        self.assertEqual(result, "breaking bad")

    def test_empty_string(self):
        self.assertEqual(self.s._remove_stop_words(""), "")

    def test_only_stop_words(self):
        result = self.s._remove_stop_words("the and of in")
        self.assertEqual(result, "")

    def test_x_connector_not_removed(self):
        # 'x' is not a stop word — keeps connector in "Fortnite x Los Simpson"
        result = self.s._remove_stop_words("fortnite x los simpson")
        self.assertIn("x", result.split())


# ---------------------------------------------------------------------------
# _word_sim
# ---------------------------------------------------------------------------

class TestWordSim(unittest.TestCase):

    def setUp(self):
        self.s = _make_searcher()

    def test_identical_words(self):
        self.assertEqual(self.s._word_sim("fortnite", "fortnite"), 1.0)

    def test_plural_suffix_s(self):
        # 'simpson' vs 'simpsons': one starts with the other, diff == 1
        score = self.s._word_sim("simpson", "simpsons")
        self.assertGreaterEqual(score, 0.92)

    def test_series_vs_serie(self):
        score = self.s._word_sim("serie", "series")
        self.assertGreaterEqual(score, 0.92)

    def test_completely_different(self):
        score = self.s._word_sim("fortnite", "breaking")
        self.assertLess(score, 0.5)

    def test_close_words(self):
        # 'color' vs 'colour': SequenceMatcher should handle
        score = self.s._word_sim("color", "colour")
        self.assertGreater(score, 0.7)

    def test_long_suffix_not_boosted(self):
        # Prefix but suffix too long (> 3 chars) → falls back to SequenceMatcher
        score = self.s._word_sim("break", "breaking")
        # Should NOT get the 0.92 prefix boost (diff = 3 chars, OK actually)
        # but either way score should reflect similarity
        self.assertGreater(score, 0.5)

    def test_symmetric(self):
        a, b = "simpsons", "simpson"
        self.assertAlmostEqual(self.s._word_sim(a, b), self.s._word_sim(b, a))


# ---------------------------------------------------------------------------
# token_match_score
# ---------------------------------------------------------------------------

class TestTokenMatchScore(unittest.TestCase):

    def setUp(self):
        self.s = _make_searcher()

    def _norm(self, t):
        return self.s.normalize_title_for_matching(t)

    def test_exact_match_after_normalization(self):
        score = self.s.token_match_score(
            self._norm("Stranger Things"),
            self._norm("Stranger Things")
        )
        self.assertAlmostEqual(score, 1.0)

    def test_fortnite_los_simpson_vs_the_simpsons(self):
        """Caso real: nombre TMDB en español vs título Hispashare en inglés."""
        score = self.s.token_match_score(
            self._norm("Fortnite x Los Simpson"),
            self._norm("Fortnite x The Simpsons")
        )
        # Debe superar 0.90 tras traducción de 'los'→'the' y match de 'simpson'/'simpsons'
        self.assertGreaterEqual(score, 0.90)

    def test_el_reino_vs_the_kingdom(self):
        """'el'→'the' translation; 'reino'≠'kingdom' so score will be moderate."""
        score = self.s.token_match_score(
            self._norm("El Reino"),
            self._norm("The Kingdom")
        )
        # Only 'the'→'the' matches after translation; 'reino'≠'kingdom' → low score
        self.assertLess(score, 0.7)

    def test_article_difference_only(self):
        """Solo difiere el artículo (los vs the), mismo contenido."""
        score = self.s.token_match_score(
            self._norm("Los Hombres de Paco"),
            self._norm("The Hombres de Paco")
        )
        # 'los'→'the' translates, 'hombres' and 'paco' match exactly
        self.assertGreaterEqual(score, 0.85)

    def test_completely_different_titles(self):
        score = self.s.token_match_score(
            self._norm("Breaking Bad"),
            self._norm("Game of Thrones")
        )
        self.assertLess(score, 0.3)

    def test_empty_candidate(self):
        score = self.s.token_match_score("", self._norm("Fortnite"))
        self.assertEqual(score, 0.0)

    def test_empty_result(self):
        score = self.s.token_match_score(self._norm("Fortnite"), "")
        self.assertEqual(score, 0.0)

    def test_plural_difference_high_score(self):
        """'serie' vs 'series' → casi perfecto."""
        score = self.s.token_match_score(
            self._norm("Dragon Ball Serie"),
            self._norm("Dragon Ball Series")
        )
        self.assertGreaterEqual(score, 0.90)

    def test_rick_y_morty_vs_rick_and_morty(self):
        """'y'→'and' translation."""
        score = self.s.token_match_score(
            self._norm("Rick y Morty"),
            self._norm("Rick and Morty")
        )
        # After translating 'y'→'and' and removing stop word 'and', both become 'rick morty'
        self.assertGreaterEqual(score, 0.90)


# ---------------------------------------------------------------------------
# build_query_candidates
# ---------------------------------------------------------------------------

class TestBuildQueryCandidates(unittest.TestCase):

    def setUp(self):
        self.s = _make_searcher()

    def test_always_includes_raw(self):
        candidates = self.s.build_query_candidates("Fortnite x Los Simpson")
        self.assertIn("Fortnite x Los Simpson", candidates)

    def test_includes_stop_word_free_variant(self):
        candidates = self.s.build_query_candidates("Fortnite x Los Simpson")
        # At least one candidate should not contain 'los'
        has_no_los = any("los" not in c.lower() for c in candidates if c != "Fortnite x Los Simpson")
        self.assertTrue(has_no_los, f"Expected a stop-word-free candidate. Got: {candidates}")

    def test_includes_translated_variant(self):
        candidates = self.s.build_query_candidates("Rick y Morty")
        # 'y'→'and' gets translated then stripped (stop word), leaving "rick morty"
        candidates_lower = [c.lower() for c in candidates]
        self.assertTrue(
            any("rick" in c and "morty" in c and "y" not in c.split() for c in candidates_lower),
            f"Expected candidate without 'y'. Got: {candidates}"
        )

    def test_no_duplicate_candidates(self):
        candidates = self.s.build_query_candidates("Breaking Bad")
        self.assertEqual(len(candidates), len(set(candidates)))

    def test_no_empty_candidates(self):
        candidates = self.s.build_query_candidates("Los Simpson")
        for c in candidates:
            self.assertTrue(c.strip(), f"Empty candidate found in {candidates}")

    def test_simple_name_without_articles(self):
        candidates = self.s.build_query_candidates("Stranger Things")
        self.assertIn("Stranger Things", candidates)

    def test_fortnite_x_los_simpson_candidates_contain_key_words(self):
        candidates = self.s.build_query_candidates("Fortnite x Los Simpson")
        found = any("fortnite" in c.lower() and "simpson" in c.lower() for c in candidates)
        self.assertTrue(found, f"Expected a candidate with 'fortnite' and 'simpson'. Got: {candidates}")


# ---------------------------------------------------------------------------
# calculate_result_score
# ---------------------------------------------------------------------------

class TestCalculateResultScore(unittest.TestCase):

    def setUp(self):
        self.s = _make_searcher()

    def _candidates(self, name):
        return self.s.build_query_candidates(name)

    def test_exact_match_scores_high(self):
        result = _result("Stranger Things (2016)", year=2016, last_pub="hace 2 días")
        score = self.s.calculate_result_score(result, self._candidates("Stranger Things"), 2016)
        self.assertGreaterEqual(score, 0.7)

    def test_fortnite_los_simpson_vs_the_simpsons_above_threshold(self):
        """Caso real: debe puntuar >= 0.42 (threshold episódico)."""
        result = _result("Fortnite x The Simpsons (2021)", year=2021, last_pub="hace 3 meses")
        score = self.s.calculate_result_score(
            result, self._candidates("Fortnite x Los Simpson"), 2021
        )
        self.assertGreaterEqual(score, 0.42, f"Score {score:.3f} debería superar el threshold 0.42")

    def test_los_simpson_vs_the_simpsons_above_threshold(self):
        result = _result("Los Simpson / The Simpsons (1989)", year=1989)
        score = self.s.calculate_result_score(
            result, self._candidates("Los Simpson"), 1989
        )
        self.assertGreaterEqual(score, 0.42)

    def test_rick_y_morty_vs_rick_and_morty_above_threshold(self):
        result = _result("Rick and Morty (2013)", year=2013, last_pub="hace 1 semana")
        score = self.s.calculate_result_score(
            result, self._candidates("Rick y Morty"), 2013
        )
        self.assertGreaterEqual(score, 0.42)

    def test_completely_different_title_below_threshold(self):
        result = _result("Game of Thrones (2011)", year=2011)
        score = self.s.calculate_result_score(
            result, self._candidates("Breaking Bad"), 2008
        )
        self.assertLess(score, 0.42)

    def test_generacion_v_vs_star_trek_next_generation_below_threshold(self):
        """Regresión: 'Generación V' emparejaba con 'Star Trek: La Nueva
        Generación' (score real en producción: 0.444, por encima del
        threshold) porque ambos comparten la palabra 'generación' y eso
        inflaba el ratio de SequenceMatcher sobre las cadenas completas.
        """
        result = _result(
            "Star Trek: La nueva generacion (Temporada Completa) (Gene Roddenberry, 1987)",
            year=1987,
            last_pub="hace 3 días",
        )
        score = self.s.calculate_result_score(
            result, self._candidates("Generación V"), None
        )
        self.assertLess(score, 0.40, f"Score {score:.3f} debería quedar por debajo de ambos thresholds")

    def test_short_candidate_inside_long_unrelated_title_below_threshold(self):
        """Un título corto que comparte una sola palabra real con un título
        largo no relacionado no debe puntuar alto solo por solapamiento de
        caracteres (p.ej. 'v' vs 'nueva')."""
        result = _result("The Nueva Generation Chronicles (2015)", year=2015)
        score = self.s.calculate_result_score(
            result, self._candidates("Generación V"), None
        )
        self.assertLess(score, 0.40)

    def test_year_mismatch_reduces_score(self):
        result_correct = _result("Breaking Bad (2008)", year=2008)
        result_wrong = _result("Breaking Bad (2015)", year=2015)
        score_correct = self.s.calculate_result_score(
            result_correct, self._candidates("Breaking Bad"), 2008
        )
        score_wrong = self.s.calculate_result_score(
            result_wrong, self._candidates("Breaking Bad"), 2008
        )
        self.assertGreater(score_correct, score_wrong)

    def test_no_year_in_result_still_scores_title(self):
        result = _result("Breaking Bad", year=None)
        score = self.s.calculate_result_score(
            result, self._candidates("Breaking Bad"), 2008
        )
        # Title component alone should be high
        self.assertGreater(score, 0.4)

    def test_plural_suffix_difference_scores_high(self):
        """'Dragon Ball Series' debe matchear bien con 'Dragon Ball Serie'."""
        result = _result("Dragon Ball Series", year=2024)
        score = self.s.calculate_result_score(
            result, self._candidates("Dragon Ball Serie"), 2024
        )
        self.assertGreaterEqual(score, 0.42)


# ---------------------------------------------------------------------------
# select_best_match (integración)
# ---------------------------------------------------------------------------

class TestSelectBestMatch(unittest.TestCase):

    def setUp(self):
        self.s = _make_searcher()

    def _candidates(self, name):
        return self.s.build_query_candidates(name)

    def test_selects_correct_result_for_los_simpson(self):
        results = [
            _result("Fortnite x The Simpsons (2021)", year=2021, last_pub="hace 2 meses"),
            _result("Algún Título Irrelevante (2020)", year=2020),
        ]
        best = self.s.select_best_match(
            results, "Fortnite x Los Simpson", 2021, 'episodic',
            self._candidates("Fortnite x Los Simpson")
        )
        self.assertIsNotNone(best)
        self.assertIn("Simpsons", best['title'])

    def test_selects_correct_result_for_rick_y_morty(self):
        results = [
            _result("Rick and Morty (2013)", year=2013, last_pub="hace 1 semana"),
            _result("Otro Título (2010)", year=2010),
        ]
        best = self.s.select_best_match(
            results, "Rick y Morty", 2013, 'episodic',
            self._candidates("Rick y Morty")
        )
        self.assertIsNotNone(best)
        self.assertIn("Rick and Morty", best['title'])

    def test_returns_none_when_no_good_match(self):
        results = [
            _result("Título Completamente Diferente (2020)", year=2020),
        ]
        best = self.s.select_best_match(
            results, "Breaking Bad", 2008, 'episodic',
            self._candidates("Breaking Bad")
        )
        self.assertIsNone(best)

    def test_best_result_has_score_set(self):
        results = [
            _result("Breaking Bad (2008)", year=2008, last_pub="hace 1 mes"),
        ]
        best = self.s.select_best_match(
            results, "Breaking Bad", 2008, 'episodic',
            self._candidates("Breaking Bad")
        )
        self.assertIsNotNone(best)
        self.assertIn('score', best)
        self.assertGreater(best['score'], 0)

    def test_empty_results_returns_none(self):
        best = self.s.select_best_match([], "Breaking Bad", 2008, 'episodic', [])
        self.assertIsNone(best)

    def _best(self, name, year, results, mode='single_file'):
        return self.s.select_best_match(results, name, year, mode, self._candidates(name))

    # Regresiones: casos reales en los que el matcher elegía la ficha de otra
    # obra y el guardado acababa en "URL ya pertenece a otra serie".

    def test_air_does_not_match_avatar_maestro_del_aire(self):
        best = self._best("Air", 2023, [
            _result("Avatar Aang: El último maestro del aire (2026)", year=2026, last_pub="hace 2 días"),
        ])
        self.assertIsNone(best)

    def test_uco_does_not_match_el_cuco(self):
        best = self._best("UCO: Las horas clave", 2024, [
            _result("El Cuco (2023)", year=2023, last_pub="hace 1 semana"),
        ])
        self.assertIsNone(best)

    def test_partial_title_containment_rejected(self):
        self.assertIsNone(self._best("La caja", 2021, [
            _result("La caja de cristal (2023)", year=2023, last_pub="hace 1 mes"),
        ]))
        self.assertIsNone(self._best("Madres", 2021, [
            _result("Madres paralelas (Pedro Almodóvar, 2021)", year=2021, last_pub="hace 2 días"),
        ]))

    def test_franchise_prefix_does_not_match_other_installment(self):
        best = self._best("Kung Fu Panda: La Leyenda de Po", 2011, [
            _result("Kung Fu Panda: El caballero del dragón (2022)", year=2022, last_pub="hace 2 días"),
        ], mode='episodic')
        self.assertIsNone(best)

    def test_reversed_word_order_rejected(self):
        best = self._best("Black Beauty", 2020, [
            _result("Beauty in Black (2024)", year=2024, last_pub="hace 2 días"),
        ])
        self.assertIsNone(best)

    def test_generic_suffix_still_matches(self):
        best = self._best("Angry Birds 2", 2019, [
            _result("Angry Birds 2: La película (2019)", year=2019),
        ])
        self.assertIsNotNone(best)

    def test_spanish_original_title_pair_matches(self):
        best = self._best("Los Simpson", 1989, [
            _result("Los Simpson / The Simpsons (1989)", year=1989),
        ], mode='episodic')
        self.assertIsNotNone(best)

    def test_spinoff_does_not_match_franchise_main_title(self):
        """El trozo antes de ':' sirve para buscar, no para puntuar."""
        self.assertIsNone(self._best("Mulán: Enciende el Fuego", 2026, [
            _result("Mulan(2020)", year=2020),
        ]))
        self.assertIsNone(self._best("My Hero Academia: Vigilantes", 2025, [
            _result("My Hero Academia (Temporadas 1-2-3-4-5-6)(2016)", year=2016),
        ], mode='episodic'))
        self.assertIsNone(self._best("La sociedad de la nieve: ¿Quiénes fuimos en la montaña?", 2024, [
            _result("La sociedad de la nieve(2023)", year=2023),
        ]))

    def test_same_movie_title_other_year_rejected(self):
        self.assertIsNone(self._best("Dogman", 2018, [
            _result("Dogman(2023)", year=2023),
        ]))

    def test_shared_generic_word_does_not_match(self):
        self.assertIsNone(self._best("La Patrulla Canina: La película", 2021, [
            _result("La Patrulla Canina: La dino película(2021)", year=2021),
        ]))

    def test_exact_title_preferred_over_partial(self):
        best = self._best("La caja", 2021, [
            _result("La caja de cristal (2023)", year=2023, last_pub="hace 1 mes"),
            _result("La caja (Lorenzo Vigas, 2021)", year=2021),
        ])
        self.assertIsNotNone(best)
        self.assertIn("Vigas", best['title'])


# ---------------------------------------------------------------------------
# content_mode fallback (_search_with_mode / search_best_result)
# ---------------------------------------------------------------------------

class TestContentModeFallback(unittest.TestCase):
    """
    Verifica que search_best_result reintenta con el modo opuesto cuando
    extract_results devuelve 0 resultados para el modo declarado.

    Caso real: "Ana y Bruno" es una película (single_file) pero el item
    tiene content_category=None → se busca como 'episodic' → 0 resultados →
    debe reintentar como 'single_file' y encontrar el match.
    """

    def setUp(self):
        from core.hispashare.searcher import HispashareSearcher
        mock_driver = MagicMock()
        self.s = HispashareSearcher(driver=mock_driver)
        # Silence disable_future_search (needs db_manager)
        self.s.db_manager = None

    def _patch_search(self, episodic_results, single_file_results):
        """Parcha perform_search (no-op) y extract_results según content_mode."""
        def fake_extract(content_mode):
            if content_mode == 'episodic':
                return list(episodic_results)
            return list(single_file_results)

        perform_patcher = patch.object(self.s, 'perform_search')
        extract_patcher = patch.object(self.s, 'extract_results', side_effect=fake_extract)
        return perform_patcher, extract_patcher

    @patch('time.sleep')
    def test_fallback_to_single_file_when_episodic_empty(self, _sleep):
        """
        Modo 'episodic' devuelve 0 resultados → fallback 'single_file' → encontrado.
        Simula "Ana y Bruno" buscada como episodic pero que es película.
        """
        movie_result = _result("Ana y Bruno (2016)", year=2016, last_pub="hace 6 meses")
        perform_p, extract_p = self._patch_search(
            episodic_results=[],
            single_file_results=[movie_result],
        )
        with perform_p, extract_p:
            best = self.s.search_best_result("Ana y Bruno", 2016, content_mode='episodic')

        self.assertIsNotNone(best)
        self.assertIn("Ana y Bruno", best['title'])

    @patch('time.sleep')
    def test_no_fallback_needed_when_primary_mode_works(self, _sleep):
        """
        Modo 'episodic' ya devuelve resultados → no se usa fallback.
        """
        series_result = _result("Breaking Bad (2008)", year=2008, last_pub="hace 1 semana")
        perform_p, extract_p = self._patch_search(
            episodic_results=[series_result],
            single_file_results=[],
        )
        with perform_p, extract_p:
            best = self.s.search_best_result("Breaking Bad", 2008, content_mode='episodic')

        self.assertIsNotNone(best)
        self.assertIn("Breaking Bad", best['title'])

    @patch('time.sleep')
    def test_returns_none_when_both_modes_empty(self, _sleep):
        """Ambos modos sin resultados → None, sin excepción."""
        perform_p, extract_p = self._patch_search(
            episodic_results=[],
            single_file_results=[],
        )
        with perform_p, extract_p:
            best = self.s.search_best_result("Título Inexistente", 2020, content_mode='episodic')

        self.assertIsNone(best)

    @patch('time.sleep')
    def test_fallback_to_episodic_when_single_file_empty(self, _sleep):
        """
        Modo 'single_file' devuelve 0 resultados → fallback 'episodic' → encontrado.
        Inverso: serie en un item que tiene content_category=movies por error.
        """
        series_result = _result("Stranger Things (2016)", year=2016, last_pub="hace 2 meses")
        perform_p, extract_p = self._patch_search(
            episodic_results=[series_result],
            single_file_results=[],
        )
        with perform_p, extract_p:
            best = self.s.search_best_result("Stranger Things", 2016, content_mode='single_file')

        self.assertIsNotNone(best)
        self.assertIn("Stranger Things", best['title'])

    @patch('time.sleep')
    def test_extract_results_called_with_correct_modes(self, _sleep):
        """Verifica que se llama primero con el modo declarado y luego con el opuesto."""
        called_modes = []

        def fake_extract(content_mode):
            called_modes.append(content_mode)
            return []

        with patch.object(self.s, 'perform_search'), \
             patch.object(self.s, 'extract_results', side_effect=fake_extract):
            self.s.search_best_result("Algo", 2020, content_mode='episodic')

        # El modo primario debe ser 'episodic', el fallback 'single_file'
        self.assertIn('episodic', called_modes)
        self.assertIn('single_file', called_modes)
        # 'episodic' debe ir primero
        self.assertLess(called_modes.index('episodic'), called_modes.index('single_file'))


if __name__ == "__main__":
    unittest.main()
