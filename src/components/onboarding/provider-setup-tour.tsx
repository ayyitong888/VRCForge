import { useEffect, useRef } from "react";
import { useTranslation } from "react-i18next";
import { driver, type DriveStep, type PopoverDOM } from "driver.js";
import "driver.js/dist/driver.css";
import "./provider-setup-tour.css";

export type ProviderSetupTourProps = {
  open: boolean;
  onReturn: () => void;
};

const targetSelectors = [
  '[data-onboarding-provider="connection"]',
  '[data-onboarding-provider="credentials"]',
  '[data-onboarding-provider="endpoint"]',
  '[data-onboarding-provider="model"]',
  '[data-onboarding-provider="actions"]',
] as const;

export function ProviderSetupTour({ open, onReturn }: ProviderSetupTourProps) {
  const { t } = useTranslation();
  const onReturnRef = useRef(onReturn);

  useEffect(() => {
    onReturnRef.current = onReturn;
  }, [onReturn]);

  useEffect(() => {
    if (!open) {
      return undefined;
    }

    let disposed = false;
    let returned = false;
    const returnToOnboarding = () => {
      if (returned || disposed) {
        return;
      }
      returned = true;
      onReturnRef.current();
    };

    const steps: DriveStep[] = [
      {
        element: targetSelectors[0],
        popover: {
          title: t("onboarding.providerTour.connectionTitle"),
          description: t("onboarding.providerTour.connectionDescription"),
        },
      },
      {
        element: targetSelectors[1],
        popover: {
          title: t("onboarding.providerTour.credentialsTitle"),
          description: t("onboarding.providerTour.credentialsDescription"),
        },
      },
      {
        element: targetSelectors[2],
        popover: {
          title: t("onboarding.providerTour.endpointTitle"),
          description: t("onboarding.providerTour.endpointDescription"),
        },
      },
      {
        element: targetSelectors[3],
        popover: {
          title: t("onboarding.providerTour.modelTitle"),
          description: t("onboarding.providerTour.modelDescription"),
        },
      },
      {
        element: targetSelectors[4],
        popover: {
          title: t("onboarding.providerTour.actionsTitle"),
          description: t("onboarding.providerTour.actionsDescription"),
        },
      },
    ];

    const tour = driver({
      steps,
      animate: !window.matchMedia("(prefers-reduced-motion: reduce)").matches,
      overlayColor: "#05070d",
      overlayOpacity: 0.62,
      smoothScroll: !window.matchMedia("(prefers-reduced-motion: reduce)").matches,
      allowClose: true,
      allowKeyboardControl: true,
      disableActiveInteraction: false,
      skipMissingElement: true,
      waitForElement: 500,
      popoverClass: "vrcforge-provider-tour",
      showProgress: true,
      progressText: t("onboarding.providerTour.progress"),
      nextBtnText: t("onboarding.providerTour.next"),
      prevBtnText: t("onboarding.providerTour.previous"),
      doneBtnText: t("onboarding.providerTour.return"),
      onPopoverRender: (popover: PopoverDOM, options) => {
        popover.closeButton.textContent = "×";
        popover.closeButton.setAttribute("aria-label", t("onboarding.providerTour.return"));
        if (options.driver.isLastStep()) return;
        const returnButton = document.createElement("button");
        returnButton.type = "button";
        returnButton.className = "vrcforge-provider-tour-return driver-popover-footer-btn";
        returnButton.textContent = t("onboarding.providerTour.return");
        returnButton.addEventListener("click", () => {
          returnToOnboarding();
          tour.destroy();
        }, { once: true });
        popover.footer.prepend(returnButton);
      },
      onCloseClick: (element, step, opts) => {
        returnToOnboarding();
        opts.driver.destroy();
      },
      onDoneClick: (element, step, opts) => {
        returnToOnboarding();
        opts.driver.destroy();
      },
      onDestroyStarted: (element, step, opts) => {
        if (!disposed) {
          returnToOnboarding();
        }
        opts.driver.destroy();
      },
      onDestroyed: () => {
        if (!disposed) {
          returnToOnboarding();
        }
      },
    });

    if (!document.querySelector(targetSelectors[0])) {
      returnToOnboarding();
      return () => {
        disposed = true;
        tour.destroy();
      };
    }

    // Settings scroll inside a pane; its scroll events do not bubble to window.
    let refreshFrame = 0;
    const refreshPosition = () => {
      if (refreshFrame || disposed) return;
      refreshFrame = window.requestAnimationFrame(() => {
        refreshFrame = 0;
        if (!disposed) tour.refresh();
      });
    };
    document.addEventListener("scroll", refreshPosition, true);
    tour.drive();
    return () => {
      disposed = true;
      document.removeEventListener("scroll", refreshPosition, true);
      window.cancelAnimationFrame(refreshFrame);
      tour.destroy();
    };
  }, [open, t]);

  return null;
}
