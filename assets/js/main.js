/* AI Compliance Radar — shared interactions
   Navigation (mobile drawer) + active link highlighting + scroll helpers
   ============================================================ */
(function () {
  'use strict';

  /* ---- Mobile navigation drawer ---- */
  var navToggle = document.querySelector('[data-nav-toggle]');
  var mobileNav = document.querySelector('[data-mobile-nav]');

  if (navToggle && mobileNav) {
    navToggle.addEventListener('click', function (e) {
      e.stopPropagation();
      var isOpen = mobileNav.classList.toggle('is-open');
      navToggle.setAttribute('aria-expanded', String(isOpen));
    });

    document.addEventListener('click', function (e) {
      if (
        mobileNav.classList.contains('is-open') &&
        !mobileNav.contains(e.target) &&
        !navToggle.contains(e.target)
      ) {
        mobileNav.classList.remove('is-open');
        navToggle.setAttribute('aria-expanded', 'false');
      }
    });

    window.addEventListener('resize', function () {
      if (window.innerWidth > 767) {
        mobileNav.classList.remove('is-open');
        navToggle.setAttribute('aria-expanded', 'false');
      }
    });
  }

  /* ---- Active navigation highlight (based on current page) ---- */
  var path = window.location.pathname.split('/').pop() || 'index.html';
  var page = path === '' ? 'index.html' : path;

  document.querySelectorAll('[data-nav-page]').forEach(function (link) {
    if (link.getAttribute('data-nav-page') === page) {
      link.classList.add('active');
    }
  });
})();
