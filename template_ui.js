const navToggle = document.querySelector(".nav-toggle");
const siteNav = document.querySelector(".site-nav");
const navLinks = document.querySelectorAll(".site-nav a");

if (navToggle && siteNav) {
  navToggle.addEventListener("click", () => {
    const expanded = navToggle.getAttribute("aria-expanded") === "true";
    navToggle.setAttribute("aria-expanded", String(!expanded));
    siteNav.classList.toggle("open");
  });
}

navLinks.forEach((link) => {
  link.addEventListener("click", () => {
    if (siteNav.classList.contains("open")) {
      siteNav.classList.remove("open");
      navToggle.setAttribute("aria-expanded", "false");
    }
  });
});
const navToggle = document.querySelector(".nav-toggle");
const siteNav = document.querySelector(".site-nav");
const navLinks = document.querySelectorAll(".site-nav a");

if (navToggle && siteNav) {
  navToggle.addEventListener("click", () => {
    const expanded = navToggle.getAttribute("aria-expanded") === "true";
    navToggle.setAttribute("aria-expanded", String(!expanded));
    siteNav.classList.toggle("open");
  });
}

navLinks.forEach((link) => {
  link.addEventListener("click", () => {
    if (siteNav.classList.contains("open")) {
      siteNav.classList.remove("open");
      navToggle.setAttribute("aria-expanded", "false");
    }
  });
});
