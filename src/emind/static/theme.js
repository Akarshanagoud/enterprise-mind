// Runs before first paint so a saved theme never flashes the wrong colours.
try {
  var saved = JSON.parse(localStorage.getItem('emind.theme'));
  if (saved === 'light' || saved === 'dark') document.documentElement.setAttribute('data-theme', saved);
} catch (e) { /* storage unavailable: follow the OS setting */ }
