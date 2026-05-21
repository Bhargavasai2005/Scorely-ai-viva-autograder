/**
 * Viva Adaptive landing page — jQuery interactions.
 */
(function ($) {
  "use strict";

  $(function () {
    var $cards = $(".viva-card");

    $cards.attr("tabindex", "0");

    function goToCtaHref($card) {
      var href = $card.find(".viva-card-cta").first().attr("href");
      if (href && href !== "#") {
        window.location.assign(href);
        return true;
      }
      return false;
    }

    $cards.on("click", function (event) {
      if ($(event.target).closest("a, button").length) {
        return;
      }
      if (goToCtaHref($(this))) {
        return;
      }
      $(this).find(".viva-card-cta").first().trigger("click");
    });

    $cards.on("keydown", function (event) {
      if (event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        if (goToCtaHref($(this))) {
          return;
        }
        $(this).find(".viva-card-cta").first().trigger("click");
      }
    });

    $(".viva-card-cta").on("click", function (event) {
      var href = $(this).attr("href");
      if (href && href !== "#") {
        return;
      }
      event.preventDefault();
      var portal = $(this).data("portal");
      $(document).trigger("viva:portal", [portal]);
    });
  });
})(window.jQuery);
