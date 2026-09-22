import { useEffect, useRef } from "react";
import { useTranslation } from "react-i18next";
import { driver, type DriveStep, type PopoverDOM } from "driver.js";
import "driver.js/dist/driver.css";
import "./provider-setup-tour.css";

export type ExternalSetupTourProps = {
  open: boolean;
  onReturn: () => void;
};

const gatewaySelector = '[data-onboarding-external="gateway"]';
const clientSelector = "[data-onboarding-client]";

export function ExternalSetupTour({ open, onReturn }: ExternalSetupTourProps) {
  const { t } = useTranslation();
  const onReturnRef = useRef(onReturn);

  useEffect(() => {
    onReturnRef.current = onReturn;
  }, [onReturn]);

  useEffect(() => {
    if (!open) return undefined;

    let disposed = false;
    let returned = false;
    let selectedClient = "";
    const returnToOnboarding = () => {
      if (returned || disposed) return;
      returned = true;
      onReturnRef.current();
    };
    const clientTarget = () => {
      const clients = Array.from(document.querySelectorAll<HTMLElement>(clientSelector));
      const selected = clients.find((row) => row.dataset.onboardingClient === selectedClient) || clients[0];
      if (selected) selectedClient = selected.dataset.onboardingClient || selectedClient;
      return selected || clientSelector;
    };
    const clientOptions = () => Array.from(document.querySelectorAll<HTMLElement>(clientSelector));
    const steps = (): DriveStep[] => [
      {
        element: gatewaySelector,
        popover: {
          title: t("onboarding.externalTour.gatewayTitle"),
          description: t("onboarding.externalTour.gatewayDescription"),
        },
      },
      {
        element: clientTarget(),
        popover: {
          title: t("onboarding.externalTour.clientTitle"),
          description: t("onboarding.externalTour.clientDescription"),
        },
      },
      {
        element: clientTarget(),
        popover: {
          title: t("onboarding.externalTour.verifyTitle"),
          description: t("onboarding.externalTour.verifyDescription"),
        },
      },
    ];

    const tour = driver({
      steps: steps(),
      // Client selection can replace the target before a highlight transition ends.
      animate: false,
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
      progressText: t("onboarding.externalTour.progress"),
      nextBtnText: t("onboarding.externalTour.next"),
      prevBtnText: t("onboarding.externalTour.previous"),
      doneBtnText: t("onboarding.externalTour.return"),
      onPopoverRender: (popover: PopoverDOM, options) => {
        popover.closeButton.textContent = "×";
        popover.closeButton.setAttribute("aria-label", t("onboarding.externalTour.return"));
        if (options.driver.getActiveIndex() === 1) {
          const available = clientOptions();
          if (available.length) {
            const label = document.createElement("label");
            label.className = "vrcforge-external-tour-client-picker";
            label.textContent = t("onboarding.externalTour.clientPicker");
            const select = document.createElement("select");
            select.className = "vrcforge-external-tour-client-select";
            select.setAttribute("aria-label", t("onboarding.externalTour.clientPicker"));
            available.forEach((row, index) => {
              const value = row.dataset.onboardingClient || String(index);
              const option = document.createElement("option");
              option.value = value;
              option.textContent = row.dataset.onboardingClientLabel || value;
              option.selected = value === selectedClient || (!selectedClient && index === 0);
              select.append(option);
            });
            selectedClient = select.value;
            select.addEventListener("change", () => {
              selectedClient = select.value;
              tour.setConfig({ ...tour.getConfig(), steps: steps() });
              tour.drive(1);
            });
            label.append(select);
            popover.description.append(label);
          }
        }
        if (options.driver.isLastStep()) return;
        const returnButton = document.createElement("button");
        returnButton.type = "button";
        returnButton.className = "vrcforge-provider-tour-return driver-popover-footer-btn";
        returnButton.textContent = t("onboarding.externalTour.return");
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
        if (!disposed) returnToOnboarding();
        opts.driver.destroy();
      },
      onDestroyed: () => {
        if (!disposed) returnToOnboarding();
      },
    });

    if (!document.querySelector(gatewaySelector)) {
      returnToOnboarding();
      return () => {
        disposed = true;
        tour.destroy();
      };
    }

    // Connector status can expand earlier rows without scrolling or resizing
    // the active row. Track its viewport rectangle for this tour's lifetime.
    let positionFrame = 0;
    let previousRect = "";
    const trackPosition = () => {
      if (disposed) return;
      const rect = tour.getActiveElement()?.getBoundingClientRect();
      const nextRect = rect ? `${rect.x},${rect.y},${rect.width},${rect.height}` : "";
      if (nextRect !== previousRect) {
        previousRect = nextRect;
        tour.refresh();
      }
      positionFrame = window.requestAnimationFrame(trackPosition);
    };
    tour.drive();
    positionFrame = window.requestAnimationFrame(trackPosition);
    return () => {
      disposed = true;
      window.cancelAnimationFrame(positionFrame);
      tour.destroy();
    };
  }, [open, t]);

  return null;
}
