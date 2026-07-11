using System;
using System.Windows;
using System.Windows.Automation;
using System.Windows.Controls;

internal static class DesktopExecutorFixture
{
    [STAThread]
    private static void Main(string[] args)
    {
        string marker = args.Length > 0 ? args[0] : "VRCForge Desktop Executor Fixture";
        var app = new Application();
        var window = new Window
        {
            Title = marker,
            Width = 560,
            Height = 270,
            WindowStartupLocation = WindowStartupLocation.CenterScreen,
            ResizeMode = ResizeMode.NoResize,
            WindowState = WindowState.Maximized
        };
        var panel = new StackPanel { Margin = new Thickness(24) };
        var input = new TextBox
        {
            Name = "FixtureInput",
            Height = 34,
            Margin = new Thickness(0, 0, 0, 16)
        };
        AutomationProperties.SetName(input, "Fixture input");
        var apply = new Button
        {
            Name = "FixtureApply",
            Content = "Apply",
            Width = 110,
            Height = 34,
            HorizontalAlignment = HorizontalAlignment.Left,
            Margin = new Thickness(0, 0, 0, 18)
        };
        AutomationProperties.SetName(apply, "Apply fixture value");
        var status = new TextBlock { Name = "FixtureStatus", Text = "Waiting" };
        AutomationProperties.SetName(status, "Fixture status");
        apply.Click += delegate
        {
            status.Text = "Applied: " + input.Text;
            AutomationProperties.SetName(status, status.Text);
        };

        panel.Children.Add(input);
        panel.Children.Add(apply);
        panel.Children.Add(status);
        window.Content = panel;
        window.Loaded += delegate { input.Focus(); };
        app.Run(window);
    }
}
