import React from "react";

/** Props for `Button`. */
export interface ButtonProps extends React.ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: "primary" | "secondary" | "outline" | "ghost" | "danger";
  size?: "sm" | "md" | "lg";
  fullWidth?: boolean;
  children: React.ReactNode;
}

/** The standard themed button used across the server web interface. */
export const Button: React.FC<ButtonProps> = ({
  variant = "primary",
  size = "md",
  fullWidth = false,
  className = "",
  children,
  disabled,
  ...props
}) => {
  const baseStyles =
    "inline-flex min-h-11 items-center justify-center rounded-lg font-medium transition-colors duration-150 focus:outline-none focus:ring-2 focus:ring-offset-2 disabled:cursor-not-allowed disabled:opacity-50 md:min-h-0";

  const variants = {
    primary: "text-white shadow-sm hover:brightness-105 active:brightness-95",
    secondary: "text-white shadow-sm hover:brightness-105 active:brightness-95",
    outline:
      "border border-gray-300 bg-white text-gray-700 hover:bg-gray-50 dark:border-gray-600 dark:bg-gray-800 dark:text-gray-200 dark:hover:bg-gray-700",
    ghost:
      "text-gray-700 hover:bg-gray-100 dark:text-gray-200 dark:hover:bg-gray-800",
    danger: "text-white shadow-sm hover:brightness-105 active:brightness-95",
  };

  const getVariantStyles = () => {
    switch (variant) {
      case "primary":
        return {
          backgroundColor: "var(--color-primary)",
          borderColor: "var(--color-primary)",
        };
      case "secondary":
        return {
          backgroundColor: "var(--color-secondary)",
          borderColor: "var(--color-secondary)",
        };
      case "danger":
        return {
          backgroundColor: "var(--color-error)",
          borderColor: "var(--color-error)",
        };
      default:
        return {};
    }
  };

  const sizes = {
    sm: "px-3 py-1.5 text-sm gap-1.5",
    md: "px-4 py-2 text-sm gap-2",
    lg: "px-5 py-2.5 text-base gap-2.5",
  };

  return (
    <button
      className={`${baseStyles} ${variants[variant]} ${sizes[size]} ${
        fullWidth ? "w-full" : ""
      } ${className}`}
      style={getVariantStyles()}
      disabled={disabled}
      {...props}
    >
      {children}
    </button>
  );
};
