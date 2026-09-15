(function () {
  "use strict";

  var refreshButton = document.getElementById("lineupRefresh");
  var refreshProgress = document.getElementById("lineupRefreshProgress");
  var csrf = document.querySelector('.lh-sync [name="csrfmiddlewaretoken"]');

  function showRefreshProgress(label) {
    refreshButton.disabled = true;
    refreshButton.innerHTML = '<i class="fas fa-spinner fa-spin"></i> Actualizando…';
    refreshProgress.hidden = false;
    refreshProgress.textContent = label || "En cola…";
  }

  function pollRefresh(jobId) {
    fetch("/alineaciones/estado/", { credentials: "same-origin" })
      .then(function (response) { return response.json(); })
      .then(function (data) {
        var job = (data.jobs || []).find(function (item) { return item.id === jobId; });
        if (!job) {
          window.setTimeout(function () { pollRefresh(jobId); }, 1200);
          return;
        }
        showRefreshProgress(job.progress_label || "Actualizando…");
        if (job.status === "queued" || job.status === "running") {
          window.setTimeout(function () { pollRefresh(jobId); }, 1200);
          return;
        }
        if (job.status === "succeeded") {
          refreshProgress.textContent = (job.card_count || 0) + " cartas actualizadas. Recargando…";
          window.setTimeout(function () { window.location.reload(); }, 350);
          return;
        }
        refreshButton.disabled = false;
        refreshButton.innerHTML = '<i class="fas fa-arrows-rotate"></i> Reintentar';
        refreshProgress.textContent = job.error || "No se pudo actualizar";
      })
      .catch(function () { window.setTimeout(function () { pollRefresh(jobId); }, 2500); });
  }

  if (refreshButton) {
    refreshButton.addEventListener("click", function () {
      showRefreshProgress("En cola para consultar tus cartas");
      fetch("/alineaciones/actualizar/", {
        method: "POST",
        credentials: "same-origin",
        headers: { "X-CSRFToken": csrf && csrf.value },
      })
        .then(function (response) { return response.json(); })
        .then(function (data) {
          if (!data.job_id) throw new Error(data.error || "No se pudo iniciar");
          pollRefresh(data.job_id);
        })
        .catch(function (error) {
          refreshButton.disabled = false;
          refreshButton.innerHTML = '<i class="fas fa-arrows-rotate"></i> Reintentar';
          refreshProgress.hidden = false;
          refreshProgress.textContent = error.message || "No se pudo iniciar";
        });
    });
  }

  var dataNode = document.getElementById("lineupCardsData");
  if (!dataNode) return;

  var cards = JSON.parse(dataNode.textContent || "[]");
  var positions = ["GK", "DEF", "MID", "FWD", "CLASSIC"];
  var chosen = {};
  cards.forEach(function (card) {
    if (card.candidate) chosen[String(card.asset_id)] = card;
  });

  var selectedInput = document.getElementById("candidateAssetIds");
  var selectedTotal = document.getElementById("selectedTotal");

  function escapeHtml(value) {
    return String(value || "").replace(/[&<>"']/g, function (char) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#039;" }[char];
    });
  }

  function photo(url) {
    return url ? '<img src="' + escapeHtml(url) + '" alt="">' : '<i class="fas fa-user"></i>';
  }

  function average(card) {
    return card.average == null ? "0" : escapeHtml(card.average);
  }

  function fixture(card) {
    if (!card.fixture_home_code || !card.fixture_away_code) {
      return '<small>' + escapeHtml(card.team) + '</small>';
    }
    var home = escapeHtml(card.fixture_home_code);
    var away = escapeHtml(card.fixture_away_code);
    if (card.fixture_is_home) home = "<b>" + home + "</b>";
    else away = "<b>" + away + "</b>";
    return '<small class="fixture">' + home + ' <i>-</i> ' + away + '</small>';
  }

  function syncSelected() {
    selectedInput.value = Object.keys(chosen).join(",");
    if (selectedTotal) selectedTotal.textContent = Object.keys(chosen).length;
  }

  function selectedAt(position) {
    return Object.keys(chosen).map(function (id) { return chosen[id]; }).filter(function (card) {
      return position === "CLASSIC" ? !card.is_in_season : card.is_in_season && card.position === position;
    });
  }

  function drawSelected(position) {
    var box = document.getElementById("lane-" + position);
    var cardsAtPosition = selectedAt(position);
    document.getElementById("count-" + position).textContent = cardsAtPosition.length;
    box.innerHTML = cardsAtPosition.length ? "" : '<span class="empty">Sin candidatas</span>';
    cardsAtPosition.forEach(function (card) {
      var row = document.createElement("article");
      row.className = "candidate";
      row.innerHTML = '<span class="candidate-photo">' + photo(card.player_picture_url) + '</span><span><strong>' + escapeHtml(card.player) + "</strong>" + fixture(card) + '</span><b class="average">' + average(card) + '</b><button type="button"><i class="fas fa-xmark"></i></button>';
      row.querySelector("button").onclick = function () {
        delete chosen[String(card.asset_id)];
        syncSelected();
        drawSelected(position);
        drawResults(position);
      };
      box.appendChild(row);
    });
  }

  function drawResults(position) {
    var input = document.querySelector('.lane-input[data-position="' + position + '"]');
    var box = document.getElementById("results-" + position);
    var term = (input.value || "").trim().toLocaleLowerCase("es");
    var matches = cards.filter(function (card) {
      if (position === "CLASSIC") {
        if (card.is_in_season) return false;
      } else if (!card.is_in_season || card.position !== position) {
        return false;
      }
      var text = (String(card.player || "") + " " + String(card.team || "")).toLocaleLowerCase("es");
      return !term || text.includes(term);
    }).slice(0, 14);
    box.innerHTML = "";
    if (!matches.length) {
      box.innerHTML = '<div class="empty">' + (term ? "No hay coincidencias" : "No hay cartas guardadas para esta posición") + "</div>";
      return;
    }
    matches.forEach(function (card) {
      var row = document.createElement("button");
      row.type = "button";
      row.className = "lane-result";
      row.disabled = Boolean(chosen[String(card.asset_id)]);
      row.innerHTML = '<span class="lane-photo">' + photo(card.player_picture_url) + '</span><span><strong>' + escapeHtml(card.player) + "</strong>" + fixture(card) + '</span><b class="average">' + average(card) + "</b>";
      row.onclick = function () {
        chosen[String(card.asset_id)] = card;
        syncSelected();
        drawSelected(position);
        drawResults(position);
      };
      box.appendChild(row);
    });
  }

  positions.forEach(function (position) {
    drawSelected(position);
    var input = document.querySelector('.lane-input[data-position="' + position + '"]');
    input.addEventListener("focus", function () { drawResults(position); });
    input.addEventListener("input", function () { drawResults(position); });
  });
  var clearButton = document.getElementById("clearCandidates");
  if (clearButton) {
    clearButton.addEventListener("click", function () {
      chosen = {};
      syncSelected();
      positions.forEach(function (position) {
        drawSelected(position);
        drawResults(position);
      });
    });
  }
  syncSelected();
}());
