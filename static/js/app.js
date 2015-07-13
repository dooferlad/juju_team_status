/**
 * Created by dooferlad on 31/05/14.
 */

var mediaApp = angular.module('mediaApp', [
  'ngRoute',
  'mediaControllers',
  'ui.bootstrap'
]);

mediaApp.config(['$routeProvider',
    function($routeProvider) {
        $routeProvider.
            when('/', {
                templateUrl: '/partials/list.html'
            }).
            when('/bugs', {
                templateUrl: '/partials/list.html'
            }).
            when('/next', {
                templateUrl: '/partials/next.html'
            }).
            otherwise({ redirectTo: '/' });
    }]);
