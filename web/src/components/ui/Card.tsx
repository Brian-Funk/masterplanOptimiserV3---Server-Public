import React from "react";

/** Props for `Card`. */
export interface CardProps extends React.HTMLAttributes<HTMLDivElement> {
  children: React.ReactNode;
  hover?: boolean;
}

/** A lightweight surface component for grouped interface content. */
export const Card: React.FC<CardProps> = ({
  children,
  className = "",
  hover = false,
  ...props
}) => {
  return (
    <div
      {...props}
      className={`
      bg-white dark:bg-gray-800 rounded-xl
      border border-gray-200 dark:border-gray-700
      ${hover ? "hover:border-gray-300 hover:shadow-sm dark:hover:border-gray-600 transition-[border-color,box-shadow] duration-150" : ""}
      ${className}
    `}
    >
      {children}
    </div>
  );
};
