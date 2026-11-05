(async () => {
  const lobby = await (await fetch("/api/lobby")).json();
  document.getElementById("balance").textContent = Number(lobby.balance).toFixed(2);
  const root = document.getElementById("games");
  for (const g of lobby.games) {
    const a = document.createElement("a");
    a.className = `game-card ${g.id}`;
    a.href = g.path;
    const img = document.createElement("img");
    img.src = g.icon;
    img.alt = "";
    const name = document.createElement("div");
    name.className = "game-name";
    name.textContent = g.title;
    const play = document.createElement("span");
    play.className = "play";
    play.textContent = `ИГРАЙ · ${g.bets[0]}–${g.bets[1]} на линия`;
    a.append(img, name, play);
    root.appendChild(a);
  }
})();
